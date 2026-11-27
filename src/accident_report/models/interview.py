"""インタビューのデータ定義。"""

from __future__ import annotations

from datetime import datetime
from enum import Enum

from pydantic import BaseModel, Field


class ItemStatus(str, Enum):
    UNCONFIRMED = "未確認"
    ANSWERED = "回答済み"
    INSUFFICIENT = "情報不足"
    CONFLICT = "矛盾あり"
    UNKNOWN = "不明・確認中"
    NOT_APPLICABLE = "該当なし"


class SourceType(str, Enum):
    EXPERIENCED = "本人の体験"
    WITNESSED = "目撃"
    HEARSAY = "伝聞"
    SPECULATION = "推測"


class Answer(BaseModel):
    """利用者の1回の回答。"""

    answer_id: str
    question_item_id: str | None  # 冒頭の自由説明は None
    question_text: str
    speaker: str = "報告者"
    raw_text: str  # 音声認識の原文（キーボード入力ならそのまま）
    text: str  # 利用者が確認・修正した文
    created_at: datetime = Field(default_factory=datetime.now)


class ValueRecord(BaseModel):
    value: str
    source_type: SourceType
    answer_id: str


class ItemState(BaseModel):
    item_id: str
    status: ItemStatus = ItemStatus.UNCONFIRMED
    value: str | None = None
    source_type: SourceType | None = None
    evidence: list[str] = Field(default_factory=list)  # 根拠の回答ID
    history: list[ValueRecord] = Field(default_factory=list)
    conflict_with: str | None = None  # 矛盾した新しい値
    conflict_asked: bool = False  # 矛盾の確認を一度聞いたか
    deferred: bool = False  # 「後で回答する」
    times_asked: int = 0
