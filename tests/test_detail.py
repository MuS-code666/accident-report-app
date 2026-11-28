"""詳細報告（原因・対策の候補、Excel転記）のテスト。"""

import openpyxl
import pytest

from accident_report.export.detail_excel import TEMPLATE, write_detail
from accident_report.models.detail import CauseItem, DetailReport, Measure, RiskRow
from accident_report.writing.detail import DetailSuggestion, report_from_notice, suggest

from test_engine import make


class FakeClient:
    def __init__(self, data):
        self.data = data

    def generate_json(self, system, prompt, schema, max_tokens=1024):
        assert schema is DetailSuggestion
        return DetailSuggestion.model_validate(self.data)


def engine_with(*answers):
    eng = make({})
    for a in answers:
        eng.next_question()
        eng.submit_answer(a)
    return eng


def test_suggestions_without_evidence_are_dropped_and_sentences_end_in_taigen():
    eng = engine_with("後ろを確認せずに下がって柱に当たりました", "通路にフレコンが置いてありました")
    client = FakeClient({
        "causes": [{"category": "人", "text": "後方確認が不足していました。", "evidence": ["A001"]},
                   {"category": "管理", "text": "教育が不足していた。", "evidence": ["A099"]}],
        "measures": [{"text": "通路に物を置かないようにする。"}]})
    causes, measures, notes = suggest(client, eng, DetailReport())
    assert [c.text for c in causes] == ["後方確認が不足。"]  # 「不足中」にしない
    assert len(causes) == 1 and causes[0].evidence == ["A001"] and causes[0].source == "AI案"
    assert "教育が不足" in notes[0]  # 記録にない回答IDを根拠にした原因は使わない
    assert measures[0].source == "AI案"


def test_report_from_notice_copies_confirmed_fields_only():
    eng = engine_with("運搬中に柱に当たりました")
    eng.states["work_content"].value = "資材の荷下ろし"
    form = {"datetime_text": "2026年11月27日 15時ごろ", "name": "鈴木", "location": "第二工場",
            "overview": ["柱に接触。"], "cause": ["確認不足と推測（報告者談）。"], "work_category": "運搬（車輌・フォーク）"}
    r = report_from_notice(form, eng, "山本")
    assert (r.datetime_text, r.name, r.location, r.work_content) == ("2026年11月27日 15時ごろ", "鈴木", "第二工場", "資材の荷下ろし")
    assert r.overview == ["柱に接触。"] and r.supervisor == "山本"
    assert r.causes == []  # 報告者の推測を、分類を決めて原因欄に入れない


@pytest.mark.skipif(not TEMPLATE.exists(), reason="暫定テンプレートがない（Git対象外）")
def test_write_detail_cells(tmp_path):
    r = DetailReport(
        datetime_text="2026年11月27日 15時ごろ", name="鈴木", location="第二工場 資材置き場", work_content="荷下ろし",
        overview=["後退中に門扉の柱に接触。"],
        causes=[CauseItem(category="人", text="右後方の確認不足。"), CauseItem(category="作業方法・環境", text="通路へのフレコン仮置き。")],
        measures=[Measure(text="通路への仮置き禁止。", owner="山本", due="2026年12月15日", status="実施中")],
        risk_score="11", risk_before=RiskRow(severity="3", likelihood="4", frequency="2", level="Ⅱ"),
        risk_after=RiskRow(severity="3", likelihood="1", frequency="2", level="Ⅲ"),
        risk_due="2026年12月15日", counterpart="なし", other="門扉は使用可能。")
    out = write_detail(r, tmp_path / "d.xlsx")
    ws = openpyxl.load_workbook(out).worksheets[0]
    assert ws["B3"].value == "2026年11月27日 15時ごろ" and ws["G3"].value == "鈴木"
    assert ws["B5"].value == "第二工場 資材置き場" and ws["F5"].value == "荷下ろし"
    assert ws["B23"].value == "・【人】右後方の確認不足。\n・【作業方法・環境】通路へのフレコン仮置き。"
    assert ws["B27"].value == "・通路への仮置き禁止。（担当：山本、期限：2026年12月15日、実施中）"
    assert ws["B32"].value == 11 and [ws[f"{c}33"].value for c in "CDEF"] == [3, 4, 2, "Ⅱ"]
    assert [ws[f"{c}34"].value for c in "CDEF"] == [3, 1, 2, "Ⅲ"]  # 入力どおり。計算しない
    assert ws["H32"].value == "2026年12月15日" and ws["B35"].value == "なし" and ws["B40"].value == "門扉は使用可能。"
    assert ws["H41"].value is None and ws["I41"].value is None and ws["J41"].value is None  # 承認欄は書かない
    assert ws["H40"].value == "社長"


def test_cause_written_like_a_measure_is_flagged():
    eng = engine_with("通路にフレコンが置いてありました")
    client = FakeClient({"causes": [{"category": "作業方法・環境", "text": "通路への物品の仮置き禁止。", "evidence": ["A001"]}],
                         "measures": []})
    _, _, notes = suggest(client, eng, DetailReport())
    assert any("対策のような書き方" in n for n in notes)
