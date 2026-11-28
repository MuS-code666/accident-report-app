"""利用者の試用で見つかった問題の再発防止テスト（2026-11-28）。"""

import openpyxl
import pytest

from accident_report.export.notice_excel import TEMPLATE, NoticeForm, write_notice
from accident_report.models.interview import ItemStatus
from accident_report.writing.notice import NoticeDraft, check_draft, draft_to_form, to_taigen

from test_engine import fact, make, result

OPENING = "昨日の午後3時ごろ、第二工場の資材置き場でフォークリフトが扉にぶつかりました"


def test_datetime_with_relative_day_and_hour_is_complete_even_if_ai_says_insufficient():
    eng = make({OPENING: result([fact("datetime", "昨日の午後3時ごろ", complete=False),
                                 fact("location", "第二工場の資材置き場", complete=False)])})
    eng.next_question()
    eng.submit_answer(OPENING)
    assert eng.states["datetime"].status == ItemStatus.ANSWERED  # Python側で日付と時刻を確認
    assert eng.states["location"].status == ItemStatus.INSUFFICIENT


def test_buttons_on_deepen_question_keep_the_value():
    eng = make({OPENING: result([fact("location", "第二工場の資材置き場", complete=False),
                                 fact("work_content", "荷下ろし", complete=False)])})
    eng.next_question()
    eng.submit_answer(OPENING)
    seen = {}
    q = eng.next_question()
    while q is not None and len(seen) < 30:
        if q.kind == "deepen":
            seen[q.item_id] = True
            eng.mark_button("confirm" if q.item_id == "location" else "dont_know")
        else:
            eng.mark_button("dont_know")
        q = eng.next_question()
    assert eng.states["location"].status == ItemStatus.ANSWERED  # 今の内容でよい
    assert eng.states["location"].value == "第二工場の資材置き場"
    assert eng.states["work_content"].status == ItemStatus.INSUFFICIENT  # 分からない: 内容は残す
    assert eng.states["work_content"].value == "荷下ろし"


def test_form_uses_recorded_datetime_even_when_marked_unknown():
    eng = make({OPENING: result([fact("datetime", "昨日の午後3時ごろ")])})
    eng.next_question()
    eng.submit_answer(OPENING)
    eng.states["datetime"].status = ItemStatus.UNKNOWN
    draft = NoticeDraft.model_validate({"work_category": "運搬（車輌・フォーク）",
                                        "overview": [{"text": "扉に接触。", "evidence": ["A001"]}]})
    form = draft_to_form(eng, draft, "鈴木")
    assert form.datetime_text.endswith("15時ごろ")


@pytest.mark.parametrize("src, expected", [
    ("シャッターの柱に接触しました。", "シャッターの柱に接触。"),
    ("フォークリフトを運転していました。", "フォークリフトを運転中。"),
    ("班長に報告した", "班長に報告。"),
    ("けが人なし。", "けが人なし。"),
    ("右後ろに擦り傷が生じた。", "右後ろに擦り傷が発生。"),
    ("班長の山本氏に事故の報告をした。", "班長の山本氏に事故の報告。"),
    ("使用可能であることを確かめた。", "使用可能であることを確認。"),
    ("右後ろが門扉の柱に当たりました。", "右後ろが門扉の柱に接触。"),
    ("扉の柱が曲がりました。", "扉の柱が変形。"),
    ("けが人はいませんでした。", "けが人なし。"),
    ("機械を止めた。後方を確認しました。", "機械を停止。後方を確認。"),
    ("終わらせるよう指示を受けた。", "終わらせるよう指示あり。"),
    ("フレコンが2つ置かれていた。", "フレコンが2つ置かれていた状態。"),
])
def test_to_taigen(src, expected):
    assert to_taigen(src) == expected


def test_check_flags_sentences_not_in_taigen():
    eng = make({})
    eng.next_question()
    eng.submit_answer("扉がへこみました")
    draft = NoticeDraft.model_validate({"work_category": "その他", "overview": [
        {"text": "扉がへこみました。", "evidence": ["A001"]}, {"text": "扉に接触。", "evidence": ["A001"]},
        {"text": "右後方の確認が不足していたとのこと。", "evidence": ["A001"]}]})
    flagged = [i.text for i in check_draft(draft, eng) if i.problem == "体言止めになっていない"]
    assert flagged == ["扉がへこみました。"]


@pytest.mark.skipif(not TEMPLATE.exists(), reason="暫定テンプレートがない（Git対象外）")
def test_exported_workbook_points_to_existing_sheet(tmp_path):
    out = write_notice(NoticeForm(overview=["扉に接触。"]), tmp_path / "o.xlsx")
    wb = openpyxl.load_workbook(out)
    assert all(v.firstSheet < len(wb.sheetnames) and v.activeTab < len(wb.sheetnames) for v in wb.views)


def test_ongoing_state_is_not_turned_into_chu():
    assert to_taigen("後方確認が不足していました。") == "後方確認が不足。"
    assert to_taigen("資材を運搬していた。") == "資材を運搬中。"
