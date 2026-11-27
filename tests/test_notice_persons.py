"""通知用「氏名」欄（事故当事者）の確認のテスト。"""

from accident_report.writing.notice import NoticeDraft, check_draft, draft_to_form

from test_engine import make


def draft(persons):
    return NoticeDraft.model_validate({
        "involved_persons": persons, "work_category": "選別",
        "overview": [{"text": "ローラーに指を挟まれた。", "evidence": ["A001"]}]})


def engine_with(answer):
    eng = make({})
    eng.next_question()
    eng.submit_answer(answer)
    return eng


def test_names_in_record_are_used_and_self_becomes_reporter():
    eng = engine_with("私と佐藤さんで清掃していて、佐藤さんが指を挟まれました")
    form = draft_to_form(eng, draft(["佐藤さん", "私"]), reporter="山田")
    assert form.name == "佐藤、山田"


def test_name_not_in_record_is_rejected_and_reported():
    eng = engine_with("作業員が指を挟まれました")
    d = draft(["鈴木"])
    assert draft_to_form(eng, d, reporter="山田").name == ""
    assert any(i.section == "involved_persons" for i in check_draft(d, eng, "山田"))


def test_reporter_added_when_they_say_they_were_driving():
    eng = engine_with("運転していたのは私です。誘導員はいませんでした。")
    assert draft_to_form(eng, draft([]), reporter="山田").name == "山田"


def test_witness_reporter_is_not_added():
    eng = engine_with("私が見ていたら、佐藤さんが指を挟まれました")
    assert draft_to_form(eng, draft(["佐藤"]), reporter="山田").name == "佐藤"
