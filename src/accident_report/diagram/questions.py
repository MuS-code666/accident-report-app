"""作図に足りない情報を見つけ、選択式の追加質問を作る（Python側）。

回答は LLM を通さず、選んだ選択肢で構造化データを直接書き換える。
画面では「重機の図＋8方向のボタン」などで指で選んでもらう想定。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

from accident_report.diagram.layout import SceneLayout
from accident_report.models.scene import DiagramSpec, Motion, Relation

DIRECTIONS = ["前", "右前", "右", "右後", "後", "左後", "左", "左前"]
DONT_KNOW = "分からない"
MACHINE_PARTS = ["バケット", "アーム", "キャビン", "クローラー", "前部", "後部", "左側", "右側"]
VEHICLE_PARTS = ["前部", "後部", "左側", "右側", "左前", "右前", "左後", "右後"]
HEAVY = {"油圧ショベル", "ホイールローダー", "その他重機"}


@dataclass
class DrawingQuestion:
    qid: str
    text: str
    choices: list[str]
    apply: Callable[[str], None]


def find_questions(spec: DiagramSpec, layouts: list[SceneLayout]) -> list[DrawingQuestion]:
    qs: list[DrawingQuestion] = []
    label = lambda eid: spec.entity(eid).label if spec.entity(eid) else eid  # noqa: E731
    anchor = spec.scenes[0].anchor if spec.scenes else None

    # 1. 左右・前後の基準が分からない位置関係
    for scene in spec.scenes:
        for rel in scene.relations:
            if rel.frame == "不明":
                choices = [f"{label(rel.reference)}の向き（運転席から見て）", "図の上（工場の図面の向き）", DONT_KNOW]

                def apply(c, rel=rel, choices=choices):
                    if c == choices[0]:
                        rel.frame, rel.certainty = "基準物の向き", "確定"
                    elif c == choices[1]:
                        rel.frame, rel.certainty = "図の上", "確定"

                if any(q.qid == f"frame:{rel.subject}:{rel.reference}" for q in qs):
                    # 同じ関係が複数の場面にある: 1回の回答をすべてに反映する
                    prev_q = next(q for q in qs if q.qid == f"frame:{rel.subject}:{rel.reference}")
                    prev_apply = prev_q.apply
                    prev_q.apply = (lambda c, a=prev_apply, b=apply: (a(c), b(c)))
                    continue
                qs.append(DrawingQuestion(
                    f"frame:{rel.subject}:{rel.reference}",
                    f"「{label(rel.subject)}は{label(rel.reference)}の{rel.direction}」の"
                    f"「{rel.direction}」は、どちらから見た向きですか？",
                    choices, apply))

    # 2. 距離が分からない位置関係
    for scene in spec.scenes:
        for rel in scene.relations:
            if rel.distance == "不明":
                choices = ["接していた", "近く（1m くらい）", "離れていた（数m）", DONT_KNOW]
                mapping = {choices[0]: "接触", choices[1]: "近い", choices[2]: "離れている"}

                def apply(c, rel=rel, mapping=mapping):
                    if c in mapping:
                        rel.distance = mapping[c]

                if any(q.qid == f"dist:{rel.subject}:{rel.reference}" for q in qs):
                    prev_q = next(q for q in qs if q.qid == f"dist:{rel.subject}:{rel.reference}")
                    prev_apply = prev_q.apply
                    prev_q.apply = (lambda c, a=prev_apply, b=apply: (a(c), b(c)))
                    continue
                qs.append(DrawingQuestion(
                    f"dist:{rel.subject}:{rel.reference}",
                    f"{label(rel.subject)}と{label(rel.reference)}は、どのくらい離れていましたか？",
                    choices, apply))

    # 3. 位置が決まらない物（最初の場面で未配置）
    if layouts and anchor:
        first = layouts[0]
        for eid in first.unplaced:
            choices = DIRECTIONS + [DONT_KNOW]

            def apply(c, eid=eid, scene=spec.scenes[0]):
                if c in DIRECTIONS:
                    scene.relations.append(Relation(
                        subject=eid, reference=anchor, direction=c, frame="基準物の向き",
                        distance="近い", certainty="確定", evidence=["追加質問"]))

            qs.append(DrawingQuestion(
                f"place:{eid}",
                f"{label(eid)}は、{label(anchor)}から見てどの方向にありましたか？（運転席から見て）",
                choices, apply))

    # 3b. 事故の要になる物（接触した相手・誘導員）の方向は、図の結果を示して必ず確認する
    if layouts and anchor:
        first = layouts[0]
        keys = [c.target for sc in spec.scenes for c in sc.contacts if c.target != anchor]
        keys += [e.id for e in spec.entities if e.kind == "誘導員"]
        for eid in dict.fromkeys(keys):
            if eid in first.unplaced or eid not in first.poses or anchor not in first.poses:
                continue
            a, t = first.poses[anchor], first.poses[eid]
            ang = math.degrees(math.atan2(t.x - a.x, -(t.y - a.y))) - a.heading
            now = DIRECTIONS[round((ang % 360) / 45) % 8]
            choices = DIRECTIONS + [DONT_KNOW]

            def apply(c, eid=eid):
                if c not in DIRECTIONS:
                    return
                for sc in spec.scenes:
                    sc.relations = [r for r in sc.relations
                                    if not ({r.subject, r.reference} == {eid, anchor})]
                spec.scenes[0].relations.append(Relation(
                    subject=eid, reference=anchor, direction=c, frame="基準物の向き",
                    distance="近い", certainty="確定", evidence=["追加質問"]))

            qs.append(DrawingQuestion(
                f"confirm:{eid}",
                f"{label(eid)}は、{label(anchor)}から見てどの方向にありましたか？"
                f"（運転席から見て。いまの図では「{now}」）",
                choices, apply))

    # 4. 接触部位が分からない
    for scene in spec.scenes:
        for c in scene.contacts:
            if c.part == "不明":
                kind = spec.entity(c.entity).kind if spec.entity(c.entity) else ""
                parts = MACHINE_PARTS if kind in HEAVY else VEHICLE_PARTS
                choices = parts + [DONT_KNOW]

                def apply(ch, c=c, parts=parts):
                    if ch in parts:
                        c.part, c.certainty = ch, "確定"

                qs.append(DrawingQuestion(
                    f"part:{c.entity}:{c.target}",
                    f"{label(c.entity)}のどの部分が{label(c.target)}に当たりましたか？",
                    choices, apply))

    # 5. 接触した物の動きが分からない
    contact_scenes = [s for s in spec.scenes if s.contacts]
    if contact_scenes:
        cs = contact_scenes[0]
        idx = spec.scenes.index(cs)
        for c in cs.contacts:
            has_motion = any(m.entity == c.entity for s in spec.scenes[: idx + 1] for m in s.motions)
            if not has_motion:
                choices = ["前進", "後退", "左旋回", "右旋回", "止まっていた", DONT_KNOW]
                target_scene = spec.scenes[idx - 1] if idx > 0 else cs

                def apply(ch, c=c, target_scene=target_scene):
                    kind = "停止" if ch == "止まっていた" else ch
                    if kind in ("前進", "後退", "左旋回", "右旋回", "停止"):
                        target_scene.motions.append(Motion(entity=c.entity, kind=kind, certainty="確定",
                                                           evidence=["追加質問"]))

                qs.append(DrawingQuestion(
                    f"motion:{c.entity}",
                    f"当たったとき、{label(c.entity)}は前進・後退・旋回のどれをしていましたか？",
                    choices, apply))
    return qs
