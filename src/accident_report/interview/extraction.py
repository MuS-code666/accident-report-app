"""回答からの情報抽出（LLM担当部分）。

LLMには「この回答から何が分かったか」だけを答えさせる。
次に何を聞くか、項目の状態をどう変えるかは engine.py（Python側）が決める。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, create_model

from accident_report.interview.questions import QuestionItem
from accident_report.llm import OllamaClient

SourceLiteral = Literal["本人の体験", "目撃", "伝聞", "推測"]

SYSTEM_PROMPT = """あなたは工場の事故報告書を作るための聞き取りを補助します。
利用者の回答から、指定された項目に当てはまる情報を抜き出してJSONで返してください。

守ること:
- 回答に書かれている情報だけを使う。書かれていないことを推測で補わない。
- 1つの回答に複数の項目の情報があれば、すべての項目について返す。ただし「確認済みの情報」と同じ内容は返さない。
- 質問された項目の情報は、必ずその項目のIDで返す。
- value は回答の言葉をできるだけそのまま使い、短い書き言葉にまとめる。人名・数字・設備名は変えない。
- source_type: 本人がしたこと・体験したことは「本人の体験」、見たことは「目撃」、人から聞いたことは「伝聞」、「〜と思う」「たぶん」は「推測」。
- complete: その項目として必要な情報がそろっていれば true。例えば日時で時刻がなければ false。
- contradicts_known: 「確認済みの情報」と食い違うときだけ true。言い方の違いだけなら false。
- 質問された項目について利用者が「分からない」「覚えていない」と答えたら dont_know を true にする。
- 質問された項目が当てはまらない（例: 指示は受けていない、けが人はいない ではなく「該当しない」）なら not_applicable を true にする。
  けが人がいない、壊れた物がない、という回答は「なし」という値として facts に入れる。
- accident_types には、回答から判断できる事故の種類を候補から選ぶ。判断できなければ空にする。"""


class _FactBase(BaseModel):
    value: str = Field(min_length=1)
    source_type: SourceLiteral
    complete: bool
    contradicts_known: bool


def make_extraction_schema(item_ids: list[str], type_ids: list[str]) -> type[BaseModel]:
    """項目IDと事故類型IDを列挙型にしたスキーマを作る（小さいモデルでも外れにくくする）。"""
    item_lit = Literal[tuple(item_ids)]  # type: ignore[valid-type]
    type_lit = Literal[tuple(type_ids or ["other"])]  # type: ignore[valid-type]
    fact = create_model("Fact", item_id=(item_lit, ...), __base__=_FactBase)
    return create_model(
        "Extraction",
        facts=(list[fact], Field(default_factory=list)),  # type: ignore[valid-type]
        accident_types=(list[type_lit], Field(default_factory=list)),  # type: ignore[valid-type]
        dont_know=(bool, False),
        not_applicable=(bool, False),
    )


def build_prompt(
    items: list[QuestionItem],
    known: dict[str, str],
    type_labels: dict[str, str],
    question: str,
    answer: str,
) -> str:
    lines = ["# 項目（id: 項目名 — 説明）"]
    lines += [f"- {it.id}: {it.label} — {it.hint}" for it in items]
    lines.append("\n# 事故の種類の候補（id: 名前）")
    lines += [f"- {k}: {v}" for k, v in type_labels.items()]
    lines.append("\n# 確認済みの情報")
    lines += [f"- {k}: {v}" for k, v in known.items()] or ["（なし）"]
    lines.append(f"\n# 質問\n{question}")
    lines.append(f"\n# 利用者の回答\n{answer}")
    return "\n".join(lines)


def extract(
    client: OllamaClient,
    items: list[QuestionItem],
    known: dict[str, str],
    type_labels: dict[str, str],
    question: str,
    answer: str,
) -> BaseModel:
    schema = make_extraction_schema([it.id for it in items], list(type_labels))
    prompt = build_prompt(items, known, type_labels, question, answer)
    return client.generate_json(SYSTEM_PROMPT, prompt, schema, max_tokens=500)
