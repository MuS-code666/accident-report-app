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
- involved_persons は事故当事者（事故を起こした人・けがをした人）の氏名だけを並べる。記録に出てくる表記のまま書き、
  役職や説明は付けない。「私」が当事者なら報告者の氏名を書く。氏名が記録になければ空にする。
- work_category は作業内容に最も近いものを選ぶ。どれにも当たらなければ「その他」とし、work_category_other に作業名を書く。"""


class Sentence(BaseModel):
    text: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class NoticeDraft(BaseModel):
    involved_persons: list[str] = Field(default_factory=list)  # 事故当事者の氏名（通知用の「氏名」欄）
    work_category: WorkCategory
    work_category_other: str | None = None
    overview: list[Sentence]
    cause: list[Sentence] = Field(default_factory=list)
    countermeasure: list[Sentence] = Field(default_factory=list)


class CheckIssue(BaseModel):
    section: str
    text: str
    problem: str


def build_prompt(engine: InterviewEngine, reporter: str = "") -> str:
    lines = [f"# 報告者の氏名: {reporter}"] if reporter else []
    lines.append("# 聞き取りの記録（回答ID: 質問 → 回答）")
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


def generate_notice(client: OllamaClient, engine: InterviewEngine, reporter: str = "") -> NoticeDraft:
    return client.generate_json(SYSTEM_PROMPT, build_prompt(engine, reporter), NoticeDraft, max_tokens=1500)


SELF_WORDS = ("私", "わたし", "自分", "本人", "僕", "俺")
_SELF = r"(?:私|わたし|自分|僕|俺)"
SELF_PARTY = re.compile(
    rf"(?:運転|操作|作業|操縦)(?:を)?(?:して)?(?:い)?た(?:の)?は{_SELF}|{_SELF}が(?:運転|操作|操縦|挟まれ|けが|怪我|転倒|当たっ|ぶつけ)"
    rf"|{_SELF}(?:は|が)?(?:事故の)?当事者")


def reporter_is_party(engine: InterviewEngine) -> bool:
    """報告者自身が事故当事者か（「報告者の立場」の回答や「運転していたのは私」などの言い回しから判定）。"""
    role = engine.states.get("reporter_role")
    if role and role.value and "当事者" in role.value and not re.search(r"目撃|見た|聞いた", role.value):
        return True
    return any(SELF_PARTY.search(a.text) for a in engine.answers)


def verified_persons(draft: NoticeDraft, engine: InterviewEngine, reporter: str = "") -> tuple[list[str], list[str]]:
    """AIが挙げた当事者の氏名を確かめる。(記録で確認できた氏名, 確認できなかった氏名) を返す。

    「私」などは報告者の氏名に置き換える。聞き取りの記録にも報告者名にも出てこない氏名は採用しない。
    """
    text = " ".join(a.text for a in engine.answers)
    ok: list[str] = []
    ng: list[str] = []
    for raw in draft.involved_persons:
        name = raw.strip().removesuffix("さん").strip()
        if not name:
            continue
        if name in SELF_WORDS:
            name = reporter
        if name and (name in text or name == reporter):
            if name not in ok:
                ok.append(name)
        elif name:
            ng.append(raw)
    if reporter and reporter not in ok and reporter_is_party(engine):
        ok.append(reporter)  # AIが「私」を拾わなかったときの補い
    return ok, ng


_NUM = re.compile(r"\d+(?:[.,]\d+)?")


def check_draft(draft: NoticeDraft, engine: InterviewEngine, reporter: str = "") -> list[CheckIssue]:
    """創作の疑いを機械的に検出する。

    - 根拠の回答IDが存在しない
    - 文中の数字が、根拠の回答にもどの回答にも出てこない
    """
    by_id = {a.answer_id: a.text for a in engine.answers}
    all_text = " ".join(by_id.values())
    issues: list[CheckIssue] = []
    _, unknown = verified_persons(draft, engine, reporter)
    for name in unknown:
        issues.append(CheckIssue(section="involved_persons", text=name, problem="聞き取りの記録にない氏名のため、氏名欄に入れていない"))
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


def draft_to_form(engine: InterviewEngine, draft: NoticeDraft, reporter: str = ""):
    """AIの下書きと聞き取り結果から、確認画面・Excel 転記用のフォームを作る。"""
    from accident_report.export.notice_excel import NoticeForm, normalize_datetime

    def value(item_id: str) -> str:
        st = engine.states.get(item_id)
        return st.value if st and st.value and st.status in (ItemStatus.ANSWERED, ItemStatus.INSUFFICIENT) else ""

    asked_on = engine.answers[0].created_at.date() if engine.answers else None
    raw_dt = value("datetime")
    return NoticeForm(
        datetime_text=(normalize_datetime(raw_dt, asked_on) or raw_dt) if raw_dt else "",
        name="、".join(verified_persons(draft, engine, reporter)[0]),  # 氏名欄は事故当事者
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
