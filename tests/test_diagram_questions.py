"""作図用の追加質問のテスト（LLMなし）。"""

from accident_report.diagram.layout import layout
from accident_report.diagram.questions import DONT_KNOW, find_questions
from accident_report.models.scene import DiagramSpec


def spec_with_gaps():
    return DiagramSpec.model_validate({
        "entities": [
            {"id": "ex", "kind": "油圧ショベル", "label": "重機", "fixed": False},
            {"id": "pillar", "kind": "柱", "label": "門の柱", "fixed": True},
            {"id": "bucket", "kind": "バケツ", "label": "ポリバケツ", "fixed": False},
        ],
        "scenes": [
            {"scene_id": "s1", "phase": "事故直前", "anchor": "ex",
             "relations": [{"subject": "pillar", "reference": "ex", "direction": "右", "frame": "不明",
                            "distance": "不明", "certainty": "未確定"}]},
            {"scene_id": "s2", "phase": "事故発生時", "anchor": "ex",
             "contacts": [{"entity": "ex", "part": "不明", "target": "pillar", "certainty": "未確定"}]},
        ],
    })


def test_finds_all_kinds_of_missing_information():
    spec = spec_with_gaps()
    qids = {q.qid.split(":")[0] for q in find_questions(spec, layout(spec))}
    assert {"frame", "dist", "place", "part", "motion"} <= qids


def test_answers_update_spec_and_remove_questions():
    spec = spec_with_gaps()
    for q in find_questions(spec, layout(spec)):
        if not q.qid.startswith("confirm:"):
            q.apply(q.choices[0])
    rel = next(r for r in spec.scenes[0].relations if r.subject == "pillar")
    assert rel.frame == "基準物の向き" and rel.distance == "接触"
    assert spec.scenes[1].contacts[0].part == "バケット"
    assert any(m.entity == "ex" for m in spec.scenes[0].motions)
    left = [q.qid for q in find_questions(spec, layout(spec))]
    assert left == ["confirm:pillar"]  # 接触した相手の方向確認だけが残る


def test_confirm_question_replaces_direction():
    spec = spec_with_gaps()
    q = next(q for q in find_questions(spec, layout(spec)) if q.qid == "confirm:pillar")
    assert "いまの図では" in q.text
    q.apply("左後")
    rels = [r for sc in spec.scenes for r in sc.relations if {r.subject, r.reference} == {"pillar", "ex"}]
    assert len(rels) == 1 and rels[0].direction == "左後" and rels[0].frame == "基準物の向き"


def test_dont_know_leaves_item_unconfirmed():
    spec = spec_with_gaps()
    for q in find_questions(spec, layout(spec)):
        q.apply(DONT_KNOW)
    assert spec.scenes[0].relations[0].frame == "不明"
    assert spec.scenes[1].contacts[0].part == "不明"
    assert "bucket" in layout(spec)[0].unplaced  # 分からない物は勝手に置かない
