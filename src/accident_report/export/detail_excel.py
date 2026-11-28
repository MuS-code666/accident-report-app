"""詳細報告（Excel）の作成。暫定テンプレート templates/detail_template.xlsx（印刷範囲 A1:J44）に転記する。

転記位置:
  B3:E4  日時            G3:J4  氏名（事故当事者）
  B5:D6  場所            F5:J6  作業内容
  B7:J22 事故概要と略図（図があれば文章の下に貼る。文字は9ptまで）
  B23:J26 原因（【分類】原因）
  B27:J31 再発防止対策（担当・期限・状態つき）
  B32    リスク評価点     C33:F33 対策前（重篤度・可能性・頻度・リスクレベル）
  H32:J34 対応期限        C34:F34 対策後
  B35:J39 相手情報        B40:G43 その他
承認欄（H40:J43 社長・役員・職長）には何も書かない。リスク評価の値は入力どおりに書き、計算はしない。
"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.styles import Alignment, Font

from accident_report import PROJECT_ROOT
from accident_report.export.notice_excel import OUTPUT_DIR, _bullets, _fit_font, _place_image, _put
from accident_report.models.detail import DetailReport

TEMPLATE = PROJECT_ROOT / "templates" / "detail_template.xlsx"
COLS = "BCDEFGHIJ"


def _measure_line(m) -> str:
    extra = [x for x in (f"担当：{m.owner}" if m.owner else "", f"期限：{m.due}" if m.due else "",
                         m.status if m.status != "未着手" else "",
                         f"完了：{m.done_on}" if m.done_on else "") if x]
    return m.text + (f"（{'、'.join(extra)}）" if extra else "")


def _center(ws, cell: str, value: str) -> None:
    ws[cell] = value
    ws[cell].font = Font(name="ＭＳ Ｐゴシック", size=11)
    ws[cell].alignment = Alignment(horizontal="center", vertical="center")


def write_detail(report: DetailReport, out_path: Path | None = None, template: Path = TEMPLATE,
                 figure: Path | None = None, extra_figure: Path | None = None) -> Path:
    if not template.exists():
        raise FileNotFoundError(
            f"テンプレートがありません: {template}\n"
            "python tools/make_templates.py 事故報告書.xlsx で作成してください。")
    wb = openpyxl.load_workbook(template)
    ws = wb.worksheets[0]

    _put(ws, "B3:E4", report.datetime_text, valign="center")
    _put(ws, "G3:J4", report.name, valign="center")
    _put(ws, "B5:D6", report.location, valign="center")
    _put(ws, "F5:J6", report.work_content, valign="center")

    overview = _bullets(report.overview)
    last = 22
    if figure:
        last = next((r for r in range(10, 17) if _fit_font(ws, overview, COLS, (7, r), sizes=(11, 10, 9))), 16)
    size = _fit_font(ws, overview, COLS, (7, last)) or 8
    _put(ws, f"B7:J{last}", overview, size=size)
    if figure:
        _place_image(ws, figure, COLS, (last + 1, 22))

    causes = "\n".join(f"・【{c.category}】{c.text}" for c in report.causes if c.text.strip())
    _put(ws, "B23:J26", causes, size=_fit_font(ws, causes, COLS, (23, 26)) or 8)
    measures = "\n".join(f"・{_measure_line(m)}" for m in report.measures if m.text.strip())
    _put(ws, "B27:J31", measures, size=_fit_font(ws, measures, COLS, (27, 31)) or 8)

    _center(ws, "B32", int(report.risk_score) if report.risk_score.strip().isdigit() else report.risk_score)
    for row, risk in ((33, report.risk_before), (34, report.risk_after)):
        for col, value in zip("CDEF", (risk.severity, risk.likelihood, risk.frequency, risk.level)):
            # 数字だけの値は数値として書く（Excel で集計しやすいように）。計算はしない
            _center(ws, f"{col}{row}", int(value) if value.strip().isdigit() else value)
    _put(ws, "H32:J34", report.risk_due, align="center", valign="center")
    _put(ws, "B35:J39", report.counterpart, size=_fit_font(ws, report.counterpart, COLS, (35, 39)) or 8)
    _put(ws, "B40:G43", report.other, size=_fit_font(ws, report.other, "BCDEFG", (40, 43)) or 8)

    if extra_figure:
        sheet = wb.create_sheet("別紙（事故状況図）")
        sheet["A1"] = "事故状況図（全場面）"
        sheet["A1"].font = Font(name="ＭＳ Ｐゴシック", size=12, bold=True)
        for c in "ABCDEFGHIJKLMN":
            sheet.column_dimensions[c].width = 9
        _place_image(sheet, extra_figure, "ABCDEFGHIJKLMN", (3, 30))
        sheet.page_setup.orientation = "landscape"
        sheet.sheet_properties.pageSetUpPr.fitToPage = True

    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    for view in wb.views:
        view.firstSheet = 0
        view.activeTab = 0
    wb.properties.creator = ""
    wb.properties.lastModifiedBy = ""
    out_path = out_path or OUTPUT_DIR / f"事故報告_詳細_{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
