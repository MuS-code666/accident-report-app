"""ルールベースの自動レイアウト（設計図 §10.4）。

位置関係（前後左右・距離の段階）から座標を決める。同じ入力からは常に同じ図になる。
角度は度数法で、0 = 図の上、時計回りが正。座標の単位は m（縮尺なしの模式図）。
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field

from accident_report.diagram.parts import PART_POINTS, PARTS
from accident_report.models.scene import DiagramSpec, Relation, SceneSpec

DIR_ANGLE = {"前": 0, "右前": 45, "右": 90, "右後": 135, "後": 180, "左後": 225, "左": 270, "左前": 315}
GAP = {"接触": 0.0, "近い": 1.0, "離れている": 3.0, "不明": 1.5}
DEFAULT_MOVE = 1.5  # 距離が分からない移動の長さ [m]
LINEAR_STRUCTURES = {"壁", "出入口", "シャッター", "門扉", "コンベヤー"}


@dataclass
class Pose:
    x: float
    y: float
    heading: float
    certain: bool = True


@dataclass
class SceneLayout:
    scene: SceneSpec
    poses: dict[str, Pose]
    unplaced: list[str]
    contact_points: list[tuple[float, float]] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def vec(angle: float) -> tuple[float, float]:
    a = math.radians(angle)
    return math.sin(a), -math.cos(a)


def size(spec: DiagramSpec, eid: str) -> tuple[float, float]:
    p = PARTS[spec.entity(eid).kind]
    return p.length, p.width


def half_extent(spec: DiagramSpec, eid: str, heading: float, direction: float) -> float:
    """向き heading の部品を、direction 方向に投影したときの半分の長さ。"""
    L, W = size(spec, eid)
    fx, fy = vec(heading)
    ux, uy = vec(direction)
    along = abs(fx * ux + fy * uy)
    across = abs(-fy * ux + fx * uy)
    return along * L / 2 + across * W / 2


def corners(spec: DiagramSpec, eid: str, pose: Pose) -> list[tuple[float, float]]:
    L, W = size(spec, eid)
    fx, fy = vec(pose.heading)
    rx, ry = -fy, fx  # 右方向
    out = []
    for sl, sw in ((-0.5, -0.5), (-0.5, 0.5), (0.5, 0.5), (0.5, -0.5)):
        # sl: 前(-) 後(+) / sw: 左(-) 右(+)
        out.append((pose.x - fx * sl * L + rx * sw * W, pose.y - fy * sl * L + ry * sw * W))
    return out


def overlap(spec: DiagramSpec, a: str, pa: Pose, b: str, pb: Pose, margin: float = 0.05) -> bool:
    """分離軸定理による回転矩形どうしの重なり判定。"""
    ca, cb = corners(spec, a, pa), corners(spec, b, pb)
    for poly in (ca, cb):
        for i in range(4):
            x1, y1 = poly[i]
            x2, y2 = poly[(i + 1) % 4]
            nx, ny = y2 - y1, x1 - x2
            pa_ = [nx * x + ny * y for x, y in ca]
            pb_ = [nx * x + ny * y for x, y in cb]
            n = math.hypot(nx, ny) or 1
            if max(pa_) + margin * n <= min(pb_) or max(pb_) + margin * n <= min(pa_):
                return False
    return True


def part_point(spec: DiagramSpec, eid: str, pose: Pose, part: str) -> tuple[float, float]:
    L, W = size(spec, eid)
    px, py = PART_POINTS.get(part, (0, 0))
    fx, fy = vec(pose.heading)
    rx, ry = -fy, fx
    return pose.x + rx * px * W - fx * py * L, pose.y + ry * px * W - fy * py * L


def closest_point_on_rect(spec: DiagramSpec, eid: str, pose: Pose, pt: tuple[float, float]) -> tuple[float, float]:
    L, W = size(spec, eid)
    fx, fy = vec(pose.heading)
    rx, ry = -fy, fx
    dx, dy = pt[0] - pose.x, pt[1] - pose.y
    lf = dx * fx + dy * fy  # 前方向の成分
    lr = dx * rx + dy * ry  # 右方向の成分
    if abs(lf) <= L / 2 and abs(lr) <= W / 2:  # 内側なら最も近い辺へ
        if L / 2 - abs(lf) < W / 2 - abs(lr):
            lf = math.copysign(L / 2, lf)
        else:
            lr = math.copysign(W / 2, lr)
    else:
        lf = max(-L / 2, min(L / 2, lf))
        lr = max(-W / 2, min(W / 2, lr))
    return pose.x + fx * lf + rx * lr, pose.y + fy * lf + ry * lr


def _relation_angle(rel: Relation, ref_pose: Pose) -> float:
    base = 0.0 if rel.frame == "図の上" else ref_pose.heading
    return (base + DIR_ANGLE[rel.direction]) % 360


def _rel_certain(rel: Relation) -> bool:
    return rel.certainty == "確定" and rel.frame != "不明" and rel.distance != "不明"


def _default_heading(spec: DiagramSpec, eid: str, anchor_heading: float, rel: Relation | None) -> float:
    kind = spec.entity(eid).kind
    if kind in LINEAR_STRUCTURES and rel is not None and rel.direction in ("左", "右"):
        return (anchor_heading + 90) % 360  # 横にある壁などは進行方向に沿って伸ばす
    return anchor_heading


def _apply_motion(pose: Pose, kind: str, dist: float | None) -> Pose:
    d = dist if dist is not None else DEFAULT_MOVE
    fx, fy = vec(pose.heading)
    if kind == "前進":
        return Pose(pose.x + fx * d, pose.y + fy * d, pose.heading, pose.certain)
    if kind == "後退":
        return Pose(pose.x - fx * d, pose.y - fy * d, pose.heading, pose.certain)
    if kind == "左旋回":
        return Pose(pose.x, pose.y, (pose.heading - 45) % 360, pose.certain)
    if kind == "右旋回":
        return Pose(pose.x, pose.y, (pose.heading + 45) % 360, pose.certain)
    return Pose(pose.x, pose.y, pose.heading, pose.certain)


def layout(spec: DiagramSpec) -> list[SceneLayout]:
    results: list[SceneLayout] = []
    fixed_poses: dict[str, Pose] = {}
    prev: SceneLayout | None = None
    ids = {e.id for e in spec.entities}
    moving = {m.entity for s in spec.scenes for m in s.motions if m.kind != "停止"}
    moving |= {c.entity for s in spec.scenes for c in s.contacts}
    static = ids - moving

    for scene in spec.scenes:
        warnings: list[str] = []
        rels = [r for r in scene.relations if r.subject in ids and r.reference in ids and r.subject != r.reference]
        for r in scene.relations:
            if r not in rels:
                warnings.append(f"位置関係を無視: {r.subject}→{r.reference}（IDが不正）")

        poses: dict[str, Pose] = {k: Pose(**vars(v)) for k, v in fixed_poses.items()}

        # 前の場面から動く物を引き継ぐ（前の場面での動きを反映）
        if prev is not None:
            for eid, p in prev.poses.items():
                if eid in poses:
                    continue
                m = next((m for m in prev.scene.motions if m.entity == eid), None)
                poses[eid] = _apply_motion(p, m.kind, m.distance_m) if m else Pose(**vars(p))

        anchor = scene.anchor if scene.anchor in ids else spec.entities[0].id
        anchor_heading = poses[anchor].heading if anchor in poses else 0.0

        # 向きの指定
        for h in scene.headings:
            if h.entity in poses or h.entity not in ids:
                continue
            ref = poses.get(h.reference)
            if ref is None:
                continue
            delta = {"基準と同じ": 0, "基準と逆": 180, "基準の左": -90, "基準の右": 90}.get(h.mode)
            if delta is None:
                continue
            poses[h.entity] = Pose(math.nan, math.nan, (ref.heading + delta) % 360, h.certainty == "確定")

        def place(eid: str, ref_id: str, rel: Relation, inverse: bool) -> None:
            ref_pose = poses[ref_id]
            heading_known = eid in poses and not math.isnan(poses[eid].heading)
            heading = poses[eid].heading if heading_known else _default_heading(spec, eid, anchor_heading, rel)
            ang = _relation_angle(rel, poses[rel.reference] if not inverse else Pose(0, 0, heading))
            if inverse:
                ang = (ang + 180) % 360
            dist = (half_extent(spec, ref_id, ref_pose.heading, ang)
                    + half_extent(spec, eid, heading, ang) + GAP[rel.distance])
            ux, uy = vec(ang)
            poses[eid] = Pose(ref_pose.x + ux * dist, ref_pose.y + uy * dist, heading, _rel_certain(rel))

        def placed(eid: str) -> bool:
            return eid in poses and not math.isnan(poses[eid].x)

        # アンカーの位置: 置ける関係がなければ原点
        if not placed(anchor):
            via = next((r for r in rels if r.subject == anchor and placed(r.reference)), None)
            if via:
                place(anchor, via.reference, via, inverse=False)
            else:
                h = poses[anchor].heading if anchor in poses else 0.0
                poses[anchor] = Pose(0.0, 0.0, h, True)

        # 位置関係をたどって配置する
        for _ in range(len(rels) + 2):
            progress = False
            for r in rels:
                if not placed(r.subject) and placed(r.reference):
                    place(r.subject, r.reference, r, inverse=False)
                    progress = True
                elif placed(r.subject) and not placed(r.reference):
                    place(r.reference, r.subject, r, inverse=True)
                    progress = True
            if not progress:
                break

        poses = {k: v for k, v in poses.items() if not math.isnan(v.x)}

        # 接触: ぶつかった部位が相手の輪郭に触れるように動かす
        contact_points = []
        contact_pairs = set()
        for c in scene.contacts:
            if c.entity not in poses or c.target not in poses:
                warnings.append(f"接触の位置を決められない: {c.entity}→{c.target}")
                continue
            mover, other = c.entity, c.target
            if spec.entity(mover).fixed and not spec.entity(other).fixed:
                mover, other = other, mover
            motion = next((m for m in scene.motions if m.entity == c.entity), None)
            if motion is None and prev is not None:
                motion = next((m for m in prev.scene.motions if m.entity == c.entity), None)
            if mover == c.entity and motion is not None and motion.kind in ("前進", "後退"):
                # 動いていた方向にまっすぐ進んで、接触部位が相手に触れる位置に置く
                poses[mover] = _contact_along_motion(spec, c.entity, poses[c.entity], c.target,
                                                     poses[c.target], c.part, motion.kind,
                                                     c.certainty == "確定")
                contact_points.append(part_point(spec, c.entity, poses[c.entity], c.part))
                contact_pairs.add(frozenset((c.entity, c.target)))
                continue
            pt = part_point(spec, c.entity, poses[c.entity], c.part)
            tgt = closest_point_on_rect(spec, c.target, poses[c.target], pt)
            dx, dy = tgt[0] - pt[0], tgt[1] - pt[1]
            if mover != c.entity:
                dx, dy = -dx, -dy
            p = poses[mover]
            poses[mover] = Pose(p.x + dx, p.y + dy, p.heading, p.certain and c.certainty == "確定")
            # 触れた部位が相手にめり込まないよう、相手の中心から離れる向きに少しずつ戻す
            pt = part_point(spec, c.entity, poses[c.entity], c.part)
            oc = poses[other]
            ux, uy = (pt[0] - oc.x, pt[1] - oc.y) if mover == c.entity else (oc.x - pt[0], oc.y - pt[1])
            n = math.hypot(ux, uy) or 1.0
            for _ in range(60):
                if not overlap(spec, mover, poses[mover], other, poses[other], margin=0.0):
                    break
                q = poses[mover]
                poses[mover] = Pose(q.x + ux / n * 0.05, q.y + uy / n * 0.05, q.heading, q.certain)
            contact_points.append(part_point(spec, c.entity, poses[c.entity], c.part))
            contact_pairs.add(frozenset((c.entity, c.target)))

        # 矛盾チェック: 動きと接触部位
        for c in scene.contacts:
            m = next((m for m in scene.motions if m.entity == c.entity), None)
            if prev is not None and m is None:
                m = next((m for m in prev.scene.motions if m.entity == c.entity), None)
            if m and m.kind == "後退" and c.part in ("前部", "左前", "右前", "バケット", "フォーク"):
                warnings.append(f"{c.entity} は後退しているのに前側（{c.part}）が接触している")
            if m and m.kind == "前進" and c.part in ("後部", "左後", "右後"):
                warnings.append(f"{c.entity} は前進しているのに後ろ側（{c.part}）が接触している")

        # 重なりの解消: 動かせる物を中心どうしを結ぶ方向へずらす
        keys = list(poses)
        for _ in range(40):
            moved = False
            for i, a in enumerate(keys):
                for b in keys[i + 1:]:
                    if frozenset((a, b)) in contact_pairs or not overlap(spec, a, poses[a], b, poses[b]):
                        continue
                    mover = _choose_mover(spec, a, b, anchor, set(fixed_poses))
                    if mover is None:
                        continue
                    other = b if mover == a else a
                    dx, dy = poses[mover].x - poses[other].x, poses[mover].y - poses[other].y
                    n = math.hypot(dx, dy) or 1.0
                    if n < 1e-6:
                        dx, dy, n = 1.0, 0.0, 1.0
                    p = poses[mover]
                    poses[mover] = Pose(p.x + dx / n * 0.3, p.y + dy / n * 0.3, p.heading, p.certain)
                    moved = True
            if not moved:
                break
        for i, a in enumerate(keys):
            for b in keys[i + 1:]:
                if frozenset((a, b)) not in contact_pairs and overlap(spec, a, poses[a], b, poses[b]):
                    warnings.append(f"{a} と {b} が重なっている")

        # 固定物と、どの場面でも動かない物は、以降の場面で位置を変えない
        for e in spec.entities:
            if (e.fixed or e.id in static) and e.id in poses and e.id not in fixed_poses:
                fixed_poses[e.id] = Pose(**vars(poses[e.id]))

        unplaced = [e.id for e in spec.entities if e.id not in poses]
        layout_ = SceneLayout(scene, poses, unplaced, contact_points, warnings)
        results.append(layout_)
        prev = layout_
    return results


def _contact_along_motion(spec: DiagramSpec, eid: str, pose: Pose, tid: str, tpose: Pose,
                          part: str, kind: str, certain: bool) -> Pose:
    """前進・後退中の接触。横方向は接触部位の側が相手にかかるように合わせ、
    縦方向は進行方向へ動かして、先端の面が相手に触れる位置にする。"""
    L, W = size(spec, eid)
    fx, fy = vec(pose.heading)
    rx, ry = -fy, fx
    tc = corners(spec, tid, tpose)
    lat = [(x - pose.x) * rx + (y - pose.y) * ry for x, y in tc]
    t_lat_c = (min(lat) + max(lat)) / 2
    px, _ = PART_POINTS.get(part, (0, 0))
    edge = px * W  # 接触部位の横位置（左 -W/2 / 中央 0 / 右 +W/2）
    if min(lat) <= edge <= max(lat):
        s_lat = 0.0  # すでに相手にかかっている
    else:
        s_lat = t_lat_c - edge
    lon = [(x - pose.x) * fx + (y - pose.y) * fy for x, y in tc]
    if kind == "後退":
        s_lon = max(lon) - (-L / 2)  # 後ろの面を相手の手前の面へ
    else:
        s_lon = min(lon) - (L / 2)  # 前の面を相手の手前の面へ
    return Pose(pose.x + rx * s_lat + fx * s_lon, pose.y + ry * s_lat + fy * s_lon,
                pose.heading, pose.certain and certain)


def _choose_mover(spec: DiagramSpec, a: str, b: str, anchor: str, frozen: set[str]) -> str | None:
    def movable(e: str) -> bool:
        return not spec.entity(e).fixed and e != anchor and e not in frozen

    if movable(b):
        return b
    if movable(a):
        return a
    return None
