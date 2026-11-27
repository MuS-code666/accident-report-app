"""通知用Excelの転記と日時の書式のテスト。"""

from datetime import date

import openpyxl
import pytest

from accident_report.export.notice_excel import TEMPLATE, NoticeForm, normalize_datetime, write_notice

TODAY = date(2026, 11, 28)


@pytest.mark.parametrize("value, expected", [
    ("10月23日の14時ごろ", "2026年10月23日 14時ごろ"),
    ("2025年3月1日 午後2時頃", "2025年3月1日 14時ごろ"),
    ("１２月５日 夕方４時", "2025年12月5日 16時ごろ"),  # 今日より後の日付にはしない
    ("きのうの夕方5時くらい", "2026年11月27日 17時ごろ"),
    ("10/23 9:30", "2026年10月23日 9時ごろ"),
    ("十月二十三日の十四時", "2026年10月23日 14時ごろ"),
    ("10月23日", "2026年10月23日"),
])
def test_normalize_datetime(value, expected):
    assert normalize_datetime(value, TODAY) == expected


def test_normalize_datetime_unreadable():
    assert normalize_datetime("先週のどこか", TODAY) is None


needs_template = pytest.mark.skipif(not TEMPLATE.exists(), reason="暫定テンプレートがない（Git対象外）")


def form(**kw):
    base = dict(datetime_text="2026年10月23日 14時ごろ", name="山田", location="第一工場 搬出口",
                work_category="運搬（車輌・フォーク）", overview=["フォークリフトで後退中、シャッターの柱に接触した。"],
                cause=["後方確認が不十分だったと思われる（報告者談）。"], countermeasure=["後退時の指差し確認を徹底する。"],
                unresolved=["事故当事者（不明・確認中）"])
    base.update(kw)
    return NoticeForm(**base)


@needs_template
def test_write_notice_cells(tmp_path):
    out = write_notice(form(), tmp_path / "out.xlsx")
    ws = openpyxl.load_workbook(out).worksheets[0]
    assert ws["B3"].value == "2026年10月23日 14時ごろ"
    assert ws["F3"].value == "山田"
    assert ws["B5"].value == "第一工場 搬出口"
    assert ws["E5"].value == "運搬（車輌・フォーク）"  # 選んだ作業名だけを残す
    assert ws["B7"].value.startswith("・フォークリフト") and "【未確認事項】事故当事者" in ws["B7"].value
    assert ws["B26"].value.startswith("・後方確認") and ws["B32"].value.startswith("・後退時")
    assert ws.print_area == "'事故報告（通知用）'!$A$1:$I$37"


@needs_template
def test_other_category_and_long_overview_goes_to_extra_sheet(tmp_path):
    long = [f"{i}番目の経過として、作業員が重機の周囲で片付けをしていたところ、状況が変わった。" * 2 for i in range(30)]
    out = write_notice(form(work_category="その他", work_category_other="清掃", overview=long), tmp_path / "o.xlsx")
    wb = openpyxl.load_workbook(out)
    assert wb.worksheets[0]["E5"].value == "その他（清掃）"
    assert "別紙（事故概要）" in wb.sheetnames
    assert wb.worksheets[0]["B7"].value.endswith("（続きは別紙）")
