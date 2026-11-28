"""詳細報告の原因分析・再発防止対策の「候補」をAIで作る。

候補は管理監督者が確認・修正してから使う（画面では「AI案」と表示する）。
聞き取りの記録にない事実は使わない。原因の各項目には根拠の回答IDを付け、Python 側で確かめる。
"""

from __future__ import annotations

from pydantic import BaseModel, Field

from accident_report.interview.engine import InterviewEngine
from accident_report.llm import OllamaClient
from accident_report.models.detail import CauseCategory, CauseItem, DetailReport, Measure
from accident_report.writing.notice import to_taigen

SYSTEM_PROMPT = """あなたは工場の安全管理者の補助として、事故の原因分析と再発防止対策の「候補」を作ります。
聞き取りの記録と報告書の下書きだけを材料にして、JSONで返してください。

守ること:
- 記録にない事実を作らない。推測で原因を断定しない。記録から読み取れる範囲の原因だけを挙げる。
- 原因は4つの分類（人・設備・機械・作業方法・環境・管理）のどれかに入れる。1つの原因は1文。
  例:「人」= 確認不足・思い込み、「設備・機械」= 死角・設備の不具合、「作業方法・環境」= 通路の狭さ・置き場所、
  「管理」= 手順・教育・指示の不足。
- 個人の責任や過失を断定する書き方をしない（「〜の不足」「〜が不十分だった可能性」）。
- 各原因の evidence に、根拠にした回答ID（A001 など）を必ず入れる。
- 対策は、原因に対応した具体的な行動にする（何を・どうする）。担当者や期限は書かない。
- 文末は体言止め（例:「後方確認の不足。」「通路への物品の仮置き禁止。」）。"""


class _Cause(BaseModel):
    category: CauseCategory
    text: str = Field(min_length=1)
    evidence: list[str] = Field(min_length=1)


class _Measure(BaseModel):
    text: str = Field(min_length=1)


class DetailSuggestion(BaseModel):
    causes: list[_Cause]
    measures: list[_Measure]


def build_prompt(engine: InterviewEngine, report: DetailReport) -> str:
    lines = ["# 聞き取りの記録（回答ID: 質問 → 回答）"]
    lines += [f"- {a.answer_id}: {a.question_text} → {a.text}" for a in engine.answers]
    lines.append("\n# 報告書の事故概要")
    lines += [f"- {x}" for x in report.overview]
    if report.causes:
        lines.append("\n# すでに書かれている原因（重複させない）")
        lines += [f"- [{c.category}] {c.text}" for c in report.causes]
    return "\n".join(lines)


def suggest(client: OllamaClient, engine: InterviewEngine, report: DetailReport
            ) -> tuple[list[CauseItem], list[Measure], list[str]]:
    """(原因の候補, 対策の候補, 注意) を返す。根拠のない原因は除いて注意に書く。"""
    out = client.generate_json(SYSTEM_PROMPT, build_prompt(engine, report), DetailSuggestion, max_tokens=1200)
    ids = {a.answer_id for a in engine.answers}
    causes, notes = [], []
    for c in out.causes:
        ev = [e for e in c.evidence if e in ids]
        if not ev:
            notes.append(f"根拠の回答がないため除外: {c.text}")
            continue
        causes.append(CauseItem(category=c.category, text=to_taigen(c.text), evidence=ev, source="AI案"))
    measures = [Measure(text=to_taigen(m.text), source="AI案") for m in out.measures]
    return causes, measures, notes


def report_from_notice(form: dict | None, engine: InterviewEngine | None, supervisor: str) -> DetailReport:
    """通知用の内容（確認済みのフォーム）から詳細報告の下書きを作る。通知用がなければ空から始める。"""
    form = form or {}
    work = ""
    if engine is not None:
        st = engine.states.get("work_content")
        work = st.value if st and st.value else ""
    # 通知用の「発生原因」「対策案」は報告者の考えなので、分類を勝手に決めて原因欄に入れない
    # （画面では参考として並べて表示し、管理監督者が取り入れる）
    return DetailReport(
        datetime_text=form.get("datetime_text", ""), name=form.get("name", ""),
        location=form.get("location", ""), work_content=work or form.get("work_category", ""),
        overview=list(form.get("overview", [])), supervisor=supervisor)
