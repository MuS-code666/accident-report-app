"""質問セット（設定ファイル）の読み込み。"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel

from accident_report import CONFIG_DIR


class QuestionItem(BaseModel):
    id: str
    label: str
    question: str
    hint: str = ""
    required: bool = False
    group: str = "fact"  # fact / cause / type
    priority: int = 100


class AccidentType(BaseModel):
    id: str
    label: str
    description: str = ""
    items: list[str]


class QuestionSet(BaseModel):
    opening_question: str
    common_items: list[QuestionItem]
    type_items: dict[str, QuestionItem]
    accident_types: dict[str, AccidentType]

    def item(self, item_id: str) -> QuestionItem:
        for it in self.common_items:
            if it.id == item_id:
                return it
        return self.type_items[item_id]


def load_question_set(directory: Path | None = None) -> QuestionSet:
    directory = directory or CONFIG_DIR / "questions"
    common = yaml.safe_load((directory / "common.yaml").read_text(encoding="utf-8"))
    types = yaml.safe_load((directory / "accident_types.yaml").read_text(encoding="utf-8"))

    type_items = {
        d["id"]: QuestionItem(group="type", required=False, **d) for d in types["items"]
    }
    accident_types = {d["id"]: AccidentType(**d) for d in types["types"]}
    for t in accident_types.values():
        missing = [i for i in t.items if i not in type_items]
        if missing:
            raise ValueError(f"事故類型 {t.id} の項目が未定義: {missing}")

    return QuestionSet(
        opening_question=common["opening"]["question"],
        common_items=[QuestionItem(**d) for d in common["items"]],
        type_items=type_items,
        accident_types=accident_types,
    )
