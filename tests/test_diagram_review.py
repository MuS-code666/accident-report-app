"""AIの作図データの点検（Python側）のテスト。"""

from accident_report.diagram.extract import number_sentences, review_spec
from accident_report.models.scene import DiagramSpec

TEXT = ("・佐藤作業員はホイールローダーで廃棄物を運んでいた。"
        "・バケットの右端が選別機の角に当たった。"
        "・誘導員の山本作業員はホイールローダーの左後ろに立っていた。")


def make_spec():
    return DiagramSpec.model_validate({
        "entities": [
            {"id": "wl", "kind": "ホイールローダー", "label": "ホイールローダー", "fixed": False},
            {"id": "sorter", "kind": "選別機", "label": "選別機", "fixed": True},
            {"id": "yamamoto", "kind": "作業員", "label": "山本作業員", "fixed": False},
        ],
        "scenes": [
            {"scene_id": "s1", "phase": "事故直前", "anchor": "wl",
             "relations": [
                 {"subject": "yamamoto", "reference": "wl", "direction": "左後", "frame": "図の上",
                  "distance": "近い", "certainty": "確定", "evidence": ["S3"]},
                 {"subject": "wl", "reference": "sorter", "direction": "右", "frame": "基準物の向き",
                  "distance": "近い", "certainty": "確定", "evidence": ["S1"]},
             ],
             "contacts": [{"entity": "wl", "part": "バケット", "target": "sorter", "certainty": "確定"}]},
            {"scene_id": "s2", "phase": "事故発生時", "anchor": "wl",
             "contacts": [{"entity": "wl", "part": "バケット", "target": "sorter", "certainty": "確定"}]},
        ],
    })


def test_review_fixes_common_llm_mistakes():
    spec = review_spec(make_spec(), number_sentences(TEXT))
    assert spec.entity("yamamoto").kind == "誘導員"
    rel_guide, rel_sorter = spec.scenes[0].relations
    assert rel_guide.frame == "不明"  # 方角の言葉がないのに「図の上」→ 確認する
    assert rel_sorter.certainty == "未確定"  # S1 に選別機は出てこない
    assert spec.scenes[0].contacts == [] and spec.scenes[1].contacts
    assert any(u.startswith("［点検］") for u in spec.unknowns)


def test_vehicle_as_subject_is_inverted_and_bystanders_dropped():
    spec = make_spec()
    spec.entities.append(spec.entities[2].model_copy(update={"id": "boss", "label": "M職長"}))
    spec = review_spec(spec, number_sentences(TEXT))
    rel = spec.scenes[0].relations[1]
    assert (rel.subject, rel.reference, rel.direction) == ("sorter", "wl", "左")
    assert spec.entity("boss") is None  # 図に関係しない人物は描かない


def test_person_anchor_is_turned_into_machine():
    spec = make_spec()
    spec.entities[0] = spec.entities[0].model_copy(update={"kind": "作業員", "label": "KMT作業員"})
    spec = review_spec(spec, number_sentences("・0.7重機でキャタピラーを出入口へ向けた。"))
    e = spec.entity("wl")
    assert e.kind == "油圧ショベル" and e.label == "油圧ショベル"
