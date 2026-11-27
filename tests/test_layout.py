"""自動レイアウトのテスト（LLMなし）。"""

import math

from accident_report.diagram.layout import layout, overlap, part_point, vec
from accident_report.models.scene import DiagramSpec


def forklift_spec(contact_part="左後", motion="後退"):
    """架空事例: フォークリフトが後退し、左後ろが柱に接触。"""
    return DiagramSpec.model_validate({
        "entities": [
            {"id": "fl", "kind": "フォークリフト", "label": "フォークリフト", "fixed": False},
            {"id": "pillar", "kind": "柱", "label": "シャッターの柱", "fixed": True},
            {"id": "bag", "kind": "フレコン", "label": "空のフレコン", "fixed": False},
        ],
        "scenes": [
            {"scene_id": "s1", "phase": "事故直前", "anchor": "fl",
             "relations": [
                 {"subject": "pillar", "reference": "fl", "direction": "左後", "frame": "基準物の向き",
                  "distance": "近い", "certainty": "確定"},
                 {"subject": "bag", "reference": "pillar", "direction": "左", "frame": "基準物の向き",
                  "distance": "接触", "certainty": "未確定"},
             ],
             "motions": [{"entity": "fl", "kind": motion, "distance_m": None, "certainty": "確定"}]},
            {"scene_id": "s2", "phase": "事故発生時", "anchor": "fl",
             "contacts": [{"entity": "fl", "part": contact_part, "target": "pillar", "certainty": "確定"}]},
        ],
    })


def test_pillar_is_left_behind_forklift():
    spec = forklift_spec()
    s1 = layout(spec)[0]
    fl, pillar = s1.poses["fl"], s1.poses["pillar"]
    fx, fy = vec(fl.heading)
    rx, ry = -fy, fx
    dx, dy = pillar.x - fl.x, pillar.y - fl.y
    assert dx * fx + dy * fy < 0  # 後ろ
    assert dx * rx + dy * ry < 0  # 左


def test_fixed_objects_keep_position_across_scenes():
    s1, s2 = layout(forklift_spec())
    assert (s1.poses["pillar"].x, s1.poses["pillar"].y) == (s2.poses["pillar"].x, s2.poses["pillar"].y)


def test_contact_part_touches_target():
    spec = forklift_spec()
    s2 = layout(spec)[1]
    pt = part_point(spec, "fl", s2.poses["fl"], "左後")
    cx, cy = s2.contact_points[0]
    assert math.hypot(pt[0] - cx, pt[1] - cy) < 1e-6


def test_no_unexpected_overlap_and_uncertain_marked():
    spec = forklift_spec()
    s1 = layout(spec)[0]
    assert not overlap(spec, "fl", s1.poses["fl"], "pillar", s1.poses["pillar"])
    assert s1.poses["bag"].certain is False  # 未確定の位置関係は未確定として残る
    assert not [w for w in s1.warnings if "重なって" in w]


def test_contradiction_between_motion_and_contact_is_reported():
    spec = forklift_spec(contact_part="前部", motion="後退")
    s2 = layout(spec)[1]
    assert any("後退しているのに前側" in w for w in s2.warnings)


def test_entity_without_relation_is_unplaced():
    spec = forklift_spec()
    spec.entities.append(spec.entities[2].model_copy(update={"id": "x", "label": "不明な物"}))
    s1 = layout(spec)[0]
    assert "x" in s1.unplaced


def test_dependents_follow_when_contact_moves_the_vehicle():
    spec = forklift_spec()
    spec.entities.append(spec.entities[0].model_copy(update={"id": "guide", "kind": "誘導員", "label": "誘導員"}))
    spec.scenes[1].relations.append(spec.scenes[0].relations[0].model_copy(
        update={"subject": "guide", "reference": "fl", "direction": "右", "distance": "近い"}))
    s2 = layout(spec)[1]
    fl, g = s2.poses["fl"], s2.poses["guide"]
    fx, fy = vec(fl.heading)
    rx, ry = -fy, fx
    assert (g.x - fl.x) * rx + (g.y - fl.y) * ry > 0  # 接触で動いた後も、誘導員はフォークの右にいる


def test_forward_motion_with_rear_corner_contact_places_target_beside():
    spec = forklift_spec(contact_part="右後", motion="前進")
    spec.scenes[0].relations[0] = spec.scenes[0].relations[0].model_copy(update={"direction": "右"})
    s2 = layout(spec)[1]
    assert not any("矛盾" in w or "前進しているのに" in w for w in s2.warnings)
    fl, pillar = s2.poses["fl"], s2.poses["pillar"]
    fx, fy = vec(fl.heading)
    rx, ry = -fy, fx
    assert (pillar.x - fl.x) * rx + (pillar.y - fl.y) * ry > 0  # 柱は車体の右横
    pt = part_point(spec, "fl", fl, "右後")
    assert math.hypot(pt[0] - s2.contact_points[0][0], pt[1] - s2.contact_points[0][1]) < 1e-6
