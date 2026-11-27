"""事故状況図の構造化データ（設計図 §10.4）。

LLMには座標を決めさせず、「何が・何の・どちら側に・どのくらい離れて」という
位置関係だけを出させる。座標は diagram/layout.py がルールで決める。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

PartKind = Literal[
    "作業員", "誘導員", "フォークリフト", "トラック", "油圧ショベル", "ホイールローダー",
    "その他重機", "コンベヤー", "破砕機", "選別機", "廃棄物", "荷物", "パレット",
    "コンテナ", "バケツ", "フレコン", "壁", "柱", "出入口", "シャッター", "門扉",
]
Direction = Literal["前", "後", "左", "右", "左前", "右前", "左後", "右後"]
# 方向の基準: 基準物の向き（例: 重機から見て左）／図の上（例: 図面の北）／不明
Frame = Literal["基準物の向き", "図の上", "不明"]
Distance = Literal["接触", "近い", "離れている", "不明"]
Certainty = Literal["確定", "未確定"]
MotionKind = Literal["前進", "後退", "左旋回", "右旋回", "停止"]
ContactPart = Literal[
    "前部", "後部", "左側", "右側", "左前", "右前", "左後", "右後",
    "バケット", "アーム", "キャビン", "クローラー", "フォーク", "不明",
]
HeadingMode = Literal["基準と同じ", "基準と逆", "基準の左", "基準の右", "不明"]
Phase = Literal["事故発生前", "事故直前", "事故発生時", "事故発生後"]


class Entity(BaseModel):
    id: str = Field(description="英数字のID。全場面で共通")
    kind: PartKind
    label: str = Field(description="図に書く名前（例: 0.7重機、門の柱）")
    fixed: bool = Field(description="壁・柱・出入口など動かない物なら true")


class Relation(BaseModel):
    subject: str = Field(description="位置を表す物のID")
    reference: str = Field(description="基準にする物のID")
    direction: Direction
    frame: Frame
    distance: Distance
    certainty: Certainty
    evidence: list[str] = Field(default_factory=list)


class Heading(BaseModel):
    entity: str
    mode: HeadingMode = Field(description="reference の向きと比べた向き")
    reference: str
    certainty: Certainty
    evidence: list[str] = Field(default_factory=list)


class Motion(BaseModel):
    entity: str
    kind: MotionKind
    distance_m: float | None = None
    certainty: Certainty
    evidence: list[str] = Field(default_factory=list)


class Contact(BaseModel):
    entity: str = Field(description="ぶつかった側（重機・車両・人）")
    part: ContactPart
    target: str = Field(description="ぶつかられた物のID")
    certainty: Certainty
    evidence: list[str] = Field(default_factory=list)


class Note(BaseModel):
    target: str
    text: str


class SceneSpec(BaseModel):
    scene_id: str
    phase: Phase
    anchor: str = Field(description="図の中心にする重機・車両のID")
    relations: list[Relation] = Field(default_factory=list)
    headings: list[Heading] = Field(default_factory=list)
    motions: list[Motion] = Field(default_factory=list, description="この場面で行っている動き")
    contacts: list[Contact] = Field(default_factory=list)
    notes: list[Note] = Field(default_factory=list)


class DiagramSpec(BaseModel):
    entities: list[Entity]
    scenes: list[SceneSpec]
    unknowns: list[str] = Field(default_factory=list, description="文章から判断できなかったこと")

    def entity(self, entity_id: str) -> Entity | None:
        return next((e for e in self.entities if e.id == entity_id), None)
