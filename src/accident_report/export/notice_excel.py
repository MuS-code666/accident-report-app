"""通知用報告書（Excel）の作成。

暫定テンプレート templates/notice_template.xlsx に転記する。印刷範囲は A1:I37。
転記するのは利用者が確認・修正した内容（NoticeForm）だけで、承認欄などは書かない。

転記位置:
  B3:D4  日時（例: 2026年10月23日 14時ごろ）
  F3:I4  氏名（事故当事者。複数なら「、」区切り）
  B5:C6  場所
  E5:I6  作業区分（選んだ作業名だけを残す）
  B7:I16 事故概要（箇条書き＋未確認事項）。B17:I25 は略図の場所として空ける（Phase 3）
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
from openpyxl.styles import Alignment, Font

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
    height = sum(ws.row_dimensions[r].height or 13.5 for r in range(rows[0], rows[1] + 1))
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


def write_notice(form: NoticeForm, out_path: Path | None = None, template: Path = TEMPLATE) -> Path:
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
    size = _fit_font(ws, overview, "BCDEFGHI", (7, 16))
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
            if _fit_font(ws, cand + "\n（続きは別紙）", "BCDEFGHI", (7, 16), sizes=(8,)) is None:
                break
            short = cand
        overview, size = short + "\n（続きは別紙）", 8
    _put(ws, "B7:I16", overview, size=size)

    for rng, lines, rows in (("B26:I31", form.cause, (26, 31)), ("B32:I37", form.countermeasure, (32, 37))):
        text = _bullets(lines)
        _put(ws, rng, text, size=_fit_font(ws, text, "BCDEFGHI", rows) or 8)

    for view in wb.views:  # 元のサンプル（2シート）の設定が残ると Excel が「修復」を求めるため直す
        view.firstSheet = 0
        view.activeTab = 0
    wb.properties.creator = ""
    wb.properties.lastModifiedBy = ""
    out_path = out_path or OUTPUT_DIR / f"事故報告_通知用_{datetime.now():%Y%m%d-%H%M%S}.xlsx"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(out_path)
    return out_path
