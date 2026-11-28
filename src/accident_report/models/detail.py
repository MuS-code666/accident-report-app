"""詳細報告（管理監督者が作る2段階目の報告）のデータ定義。

リスク評価は会社の基準で手入力する。値の自動計算はしない（記入例から計算式を推測しない）。
承認欄（社長・役員・職長）はアプリでは扱わない。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

CauseCategory = Literal["人", "設備・機械", "作業方法・環境", "管理"]
CAUSE_CATEGORIES: list[str] = ["人", "設備・機械", "作業方法・環境", "管理"]
MeasureStatus = Literal["未着手", "実施中", "完了"]
MEASURE_STATUSES: list[str] = ["未着手", "実施中", "完了"]


class CauseItem(BaseModel):
    category: CauseCategory = "人"
    text: str = ""
    evidence: list[str] = Field(default_factory=list)  # 根拠の回答ID
    source: Literal["AI案", "手入力"] = "手入力"


class Measure(BaseModel):
    text: str = ""
    owner: str = ""  # 担当
    due: str = ""  # 期限（例: 2026年12月15日）
    status: MeasureStatus = "未着手"
    done_on: str = ""  # 完了日
    source: Literal["AI案", "手入力"] = "手入力"


class RiskRow(BaseModel):
    severity: str = ""  # 重篤度
    likelihood: str = ""  # 可能性
    frequency: str = ""  # 頻度
    level: str = ""  # リスクレベル（例: Ⅱ）


class DetailReport(BaseModel):
    datetime_text: str = ""
    name: str = ""  # 事故当事者
    location: str = ""
    work_content: str = ""
    overview: list[str] = Field(default_factory=list)
    causes: list[CauseItem] = Field(default_factory=list)
    measures: list[Measure] = Field(default_factory=list)
    risk_score: str = ""  # リスク評価点（B32）
    risk_before: RiskRow = Field(default_factory=RiskRow)
    risk_after: RiskRow = Field(default_factory=RiskRow)
    risk_due: str = ""  # 対応期限
    counterpart: str = ""  # 相手情報
    other: str = ""  # その他
    supervisor: str = ""  # 詳細報告を作った管理監督者
