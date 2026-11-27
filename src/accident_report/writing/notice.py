"""通知用報告書の文章生成と根拠チェック。"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field

from accident_report.interview.engine import InterviewEngine
from accident_report.llm import OllamaClient
from accident_report.models.interview import ItemStatus

WorkCategory = Literal["運搬（車輌・フォーク）", "積降し", "選別", "破砕", "その他"]

SYSTEM_PROMPT = """あなたは工場の事故報告書（通知用）の文章を作ります。
聞き取りの記録だけを材料に、書面にふさわしい文章へ整理してJSONで返してください。

守ること:
- 聞き取っていない事実を補ったり創作したりしない。
- 人名・重機名・日時・数量は記録の表記をそのまま使う。
- 口語を書き言葉にし、主語・対象物・動作をはっきり書く。冗長な表現や重複を除く。
- overview（事故概要）は時系列の箇条書き。事故前の作業、作業指示や作業開始の経緯、事故直前の動作、事故発生時の状況、被害、事故後の対応の順に、記録にある分だけ書く。
  見出しや項目名は書かず、各文には具体的な内容（誰が・何を・どうした）を書く。
- 伝聞は「〜とのこと」、推測は「〜と思われる（報告者談）」のように区別して書く。
- 事故当事者の責任や過失を断定しない。
- 分からない事項は無理に文章にしない。
- 各文の evidence には、根拠にした回答ID（A001 など）を必ず入れる。
- cause と countermeasure は報告者が述べた内容だけを書く。述べていなければ空にする。
- work_category は作業内容に最も近いものを選ぶ。どれにも当たらなければ「その他」とし、work_category_other に作業名を書く。"""


class Sentence(BaseModel):
    text: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class NoticeDraft(BaseModel):
    work_category: WorkCategory
    work_category_other: str | None = None
    overview: list[Sentence]
    cause: list[Sentence] = Field(default_factory=list)
    countermeasure: list[Sentence] = Field(default_factory=list)


class CheckIssue(BaseModel):
    section: str
    text: str
    problem: str


def build_prompt(engine: InterviewEngine) -> str:
    lines = ["# 聞き取りの記録（回答ID: 質問 → 回答）"]
    for a in engine.answers:
        lines.append(f"- {a.answer_id}: {a.question_text} → {a.text}")
    lines.append("\n# 整理済みの項目（項目: 値 [情報の種類]）")
    for item_id, st in engine.states.items():
        if st.value and st.status in (ItemStatus.ANSWERED, ItemStatus.INSUFFICIENT):
            label = engine.qset.item(item_id).label
            lines.append(f"- {label}: {st.value} [{st.source_type.value if st.source_type else ''}]")
    unresolved = engine.unresolved()
    if unresolved:
        lines.append("\n# 未確認の事項（文章にしない）")
        lines += [f"- {label}（{status.value}）" for label, status in unresolved]
    return "\n".join(lines)


def generate_notice(client: OllamaClient, engine: InterviewEngine) -> NoticeDraft:
    return client.generate_json(SYSTEM_PROMPT, build_prompt(engine), NoticeDraft, max_tokens=1500)


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def check_draft(draft: NoticeDraft, engine: InterviewEngine) -> list[CheckIssue]:
    """創作の疑いを機械的に検出する。

    - 根拠の回答IDが存在しない
    - 文中の数字が、根拠の回答にもどの回答にも出てこない
    """
    by_id = {a.answer_id: a.text for a in engine.answers}
    all_text = " ".join(by_id.values())
    issues: list[CheckIssue] = []
    for section in ("overview", "cause", "countermeasure"):
        for s in getattr(draft, section):
            missing = [e for e in s.evidence if e not in by_id]
            if missing:
                issues.append(CheckIssue(section=section, text=s.text,
                                         problem=f"存在しない回答IDを根拠にしている: {missing}"))
            cited = " ".join(by_id.get(e, "") for e in s.evidence)
            for num in _NUM.findall(s.text):
                if num not in cited and num not in all_text and _to_full(num) not in all_text:
                    issues.append(CheckIssue(section=section, text=s.text,
                                             problem=f"回答にない数字「{num}」がある"))
    return issues


def draft_to_form(engine: InterviewEngine, draft: NoticeDraft, name: str):
    """AIの下書きと聞き取り結果から、確認画面・Excel 転記用のフォームを作る。"""
    from accident_report.export.notice_excel import NoticeForm, normalize_datetime

    def value(item_id: str) -> str:
        st = engine.states.get(item_id)
        return st.value if st and st.value and st.status in (ItemStatus.ANSWERED, ItemStatus.INSUFFICIENT) else ""

    asked_on = engine.answers[0].created_at.date() if engine.answers else None
    raw_dt = value("datetime")
    return NoticeForm(
        datetime_text=(normalize_datetime(raw_dt, asked_on) or raw_dt) if raw_dt else "",
        name=name,
        location=value("location"),
        work_category=draft.work_category,
        work_category_other=draft.work_category_other or "",
        overview=[s.text for s in draft.overview],
        cause=[s.text for s in draft.cause],
        countermeasure=[s.text for s in draft.countermeasure],
        unresolved=[f"{label}（{status.value}）" for label, status in engine.unresolved()],
    )


def _to_full(s: str) -> str:
    return s.translate(str.maketrans("0123456789.", "０１２３４５６７８９．"))
