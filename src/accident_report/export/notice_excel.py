"""通知用報告書（Excel）の作成。

暫定テンプレート templates/notice_template.xlsx に転記する。印刷範囲は A1:I37。
転記するのは利用者が確認・修正した内容（NoticeForm）だけで、承認欄などは書かない。

転記位置:
  B3:D4  日時（例: 2026年10月23日 14時ごろ）
  F3:I4  氏名（事故当事者。複数なら「、」区切り）
  B5:C6  場所
  E5:I6  作業区分（選んだ作業名だけを残す）
  B7:I25 事故概要（箇条書き＋未確認事項）と略図。
         図がないとき: 文章は B7:I16、B17:I25 は手書きの略図用に空ける。
         図があるとき: 文章が入る最小の行まで（B7:I11〜B7:I16）を文章にし、その下を図にする（文字は9ptまで）。
         3場面以上の図は、全場面を「別紙（事故状況図）」シートにも載せる。
  B26:I31 発生原因
  B32:I37 対策案
概要が枠に入りきらないときは文字を小さくし、それでも入らなければ「別紙」シートに全文を書く。
"""

from __future__ import annotations

import math
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

import openpyxl
from openpyxl.drawing.image import Image as XLImage
from openpyxl.drawing.spreadsheet_drawing import AnchorMarker, OneCellAnchor
from openpyxl.drawing.xdr import XDRPositiveSize2D
from openpyxl.styles import Alignment, Font
from openpyxl.utils import column_index_from_string
from openpyxl.utils.units import pixels_to_EMU

from accident_report import PROJECT_ROOT
from accident_report.dates import normalize_datetime  # noqa: F401 - 互換のため再公開

TEMPLATE = PROJECT_ROOT / "templates" / "notice_template.xlsx"
OUTPUT_DIR = PROJECT_ROOT / "output"

WORK_CATEGORIES = ["運搬（車輌・フォーク）", "積降し", "選別", "破砕", "その他"]


@dataclass
class NoticeForm:
    datetime_text: str = ""
    name: str = ""
    location: str = ""
    work_category: str = ""  # WORK_CATEGORIES のどれか
    work_category_other: str = ""
    overview: list[str] = field(default_factory=list)
    cause: list[str] = field(default_factory=list)
    countermeasure: list[str] = field(default_factory=list)
    unresolved: list[str] = field(default_factory=list)  # 「発生日時（不明・確認中）」など

    def work_category_text(self) -> str:
        if self.work_category == "その他":
            return f"その他（{self.work_category_other}）" if self.work_category_other else "その他"
        return self.work_category


# ---- Excel への転記 ---------------------------------------------------

def _bullets(lines: list[str]) -> str:
    return "\n".join(f"・{x.strip().lstrip('・')}" for x in lines if x.strip())


def _overview_text(form: NoticeForm) -> str:
    text = _bullets(form.overview)
    if form.unresolved:
        text += ("\n" if text else "") + "【未確認事項】" + "、".join(form.unresolved)
    return text


def _width_units(s: str) -> int:
    return sum(2 if unicodedata.east_asian_width(ch) in "WFA" else 1 for ch in s)


def estimate_lines(text: str, width_chars: float) -> int:
    """全角を2、半角を1として、折り返し後の行数を見積もる。"""
    return sum(max(1, math.ceil(_width_units(line) / (width_chars * 2))) for line in text.split("\n"))


def _area_size(ws, cols: str, rows: tuple[int, int]) -> tuple[float, float]:
    """範囲の幅（Excel の列幅の合計）と高さ（pt）。"""
    width = sum(ws.column_dimensions[c].width or 8.43 for c in cols)
    height = sum(_row_pt(ws, r) for r in range(rows[0], rows[1] + 1))
    return width, height


def _fit_font(ws, text: str, cols: str, rows: tuple[int, int], sizes=(11, 10, 9, 8)) -> int | None:
    width, height = _area_size(ws, cols, rows)
    for sz in sizes:
        chars_per_line = width * 11 / sz / 2 * 0.95  # 列幅は半角文字数。全角は2文字分
        if estimate_lines(text, chars_per_line) * sz * 1.35 <= height:
            return sz
    return None


def _put(ws, rng: str, text: str, size: int = 11, align: str = "left", valign: str = "top") -> None:
    if rng not in {str(m) for m in ws.merged_cells.ranges}:
        ws.merge_cells(rng)
    cell = ws[rng.split(":")[0]]
    cell.value = text
    cell.font = Font(name="ＭＳ Ｐゴシック", size=size)
    cell.alignment = Alignment(horizontal=align, vertical=valign, wrap_text=True)


def _col_px(ws, col: str) -> float:
    return (ws.column_dimensions[col].width or 8.43) * 7 + 5


def _row_pt(ws, row: int) -> float:
    """行の高さ（pt）。指定がなければシートの標準の高さ（テンプレートでは 12.75pt）。"""
    return ws.row_dimensions[row].height or ws.sheet_format.defaultRowHeight or 13.5


def _row_px(ws, row: int) -> float:
    return _row_pt(ws, row) * 4 / 3


def _place_image(ws, path: Path, cols: str, rows: tuple[int, int], margin: float = 8) -> XLImage:
    """範囲に収まるよう縦横比を保って縮小し、範囲の中央に貼る。"""
    img = XLImage(str(path))
    area_w = sum(_col_px(ws, c) for c in cols) - margin * 2
    area_h = sum(_row_px(ws, r) for r in range(rows[0], rows[1] + 1)) - margin * 2
    scale = min(area_w / img.width, area_h / img.height)
    img.width, img.height = img.width * scale, img.height * scale
    # 左上の位置（中央寄せ）を、列・行とその中のずれに直す
    x = margin + (area_w - img.width) / 2
    y = margin + (area_h - img.height) / 2
    col_i = 0
    while col_i < len(cols) - 1 and x >= _col_px(ws, cols[col_i]):
        x -= _col_px(ws, cols[col_i])
        col_i += 1
    row = rows[0]
    while row < rows[1] and y >= _row_px(ws, row):
        y -= _row_px(ws, row)
        row += 1
    marker = AnchorMarker(col=column_index_from_string(cols[col_i]) - 1, colOff=pixels_to_EMU(int(x)),
                          row=row - 1, rowOff=pixels_to_EMU(int(y)))
    img.anchor = OneCellAnchor(_from=marker, ext=XDRPositiveSize2D(pixels_to_EMU(int(img.width)),
                                                                  pixels_to_EMU(int(img.height))))
    ws.add_image(img)
    return img


def _text_last_row(ws, text: str) -> int:
    """図があるとき、文章に使う最後の行（文章が9pt以上で入る最小の行。図をできるだけ大きくする）。"""
    for last in range(11, 17):
        if _fit_font(ws, text, "BCDEFGHI", (7, last), sizes=(11, 10, 9)):
            return last
    return 16


def write_notice(form: NoticeForm, out_path: Path | None = None, template: Path = TEMPLATE,
                 figure: Path | None = None, extra_figure: Path | None = None) -> Path:
    if not template.exists():
        raise FileNotFoundError(
            f"テンプレートがありません: {template}\n"
            "python tools/make_templates.py 事故報告書.xlsx で作成してください。")
    wb = openpyxl.load_workbook(template)
    ws = wb.worksheets[0]

    _put(ws, "B3:D4", form.datetime_text, valign="center")
    _put(ws, "F3:I4", form.name, valign="center")
    _put(ws, "B5:C6", form.location, valign="center")
    _put(ws, "E5:I6", form.work_category_text(), align="center", valign="center")

    overview = _overview_text(form)
    last = _text_last_row(ws, overview) if figure else 16
    size = _fit_font(ws, overview, "BCDEFGHI", (7, last))
    if size is None:
        extra = wb.create_sheet("別紙（事故概要）")
        extra.column_dimensions["A"].width = 100
        extra["A1"] = "事故概要（全文）"
        extra["A1"].font = Font(name="ＭＳ Ｐゴシック", size=12, bold=True)
        extra["A2"] = overview
        extra["A2"].font = Font(name="ＭＳ Ｐゴシック", size=10.5)
        extra["A2"].alignment = Alignment(vertical="top", wrap_text=True)
        extra.row_dimensions[2].height = min(409, estimate_lines(overview, 95) * 15)
        lines = overview.split("\n")
        short = ""
        for ln in lines:  # 枠に入る分だけ載せて、続きは別紙
            cand = (short + "\n" + ln) if short else ln
            if _fit_font(ws, cand + "\n（続きは別紙）", "BCDEFGHI", (7, last), sizes=(8,)) is None:
                break
            short = cand
        overview, size = short + "\n（続きは別紙）", 8
    _put(ws, f"B7:I{last}", overview, size=size)
    if figure:
        _place_image(ws, figure, "BCDEFGHI", (last + 1, 25))
    if extra_figure:
        sheet = wb.create_sheet("別紙（事故状況図）")
        sheet["A1"] = "事故状況図（全場面）"
        sheet["A1"].font = Font(name="ＭＳ Ｐゴシック", size=12, bold=True)
        for c in "ABCDEFGHIJKLMN":
            sheet.column_dimensions[c].width = 9
        _place_image(sheet, extra_figure, "ABCDEFGHIJKLMN", (3, 30))
        sheet.page_setup.orientation = "landscape"
        sheet.page_setup.fitToWidth = 1
        sheet.sheet_properties.pageSetUpPr.fitToPage = True

    for rng, lines, rows in (("B26:I31", form.cause, (26, 31)), ("B32:I37", form.countermeasure, (32, 37))):
        text = _bullets(lines)
        _put(ws, rng, text, size=_fit_font(ws, text, "BCDEFGHI", rows) or 8)

    # A4縦1枚に収めて印刷する（テンプレートの倍率106%のままでは横にはみ出して2枚に分かれる）
    ws.page_setup.paperSize = ws.PAPERSIZE_A4
    ws.page_setup.orientation = "portrait"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 1
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    for view in wb.views:  # 元のサンプル（2シート）の設定が残ると Excel が「修復」を求めるため直す
        view.firstSheet = 0
        view.activeTab = 0
    wb.properties.creator = ""
    wb.properties.lastModifiedBy = ""
    out_path = out_path or OUTPUT_DIR / f"事故報告_通知用_{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
