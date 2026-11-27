"""記入済みの事故報告書サンプルから、暫定の空欄テンプレートを作成する。

使い方:
    python tools/make_templates.py <サンプル.xlsx> [出力フォルダ]

出力:
    <出力フォルダ>/notice_template.xlsx  … 事故報告（通知用）
    <出力フォルダ>/detail_template.xlsx  … 事故報告（詳細）

openpyxl は図形・グループ化画像を読み込まないため、保存時に
サンプルの事故状況図・写真はすべて除去される（意図した動作）。
正式な空欄テンプレートが提供されたら、そちらに置き換えること。
"""

from __future__ import annotations

import sys
import zipfile
from pathlib import Path

import openpyxl
from openpyxl.styles import Border

NOTICE_SHEET = "事故報告（通知用）"
DETAIL_SHEET_INDEX = 1
DETAIL_SHEET_TITLE = "事故報告（詳細）"

# 詳細報告で個別の事故情報が入っているセル（結合範囲は左上セル）
DETAIL_VALUE_CELLS = [
    "B3",  # 日時
    "G3",  # 氏名
    "B5",  # 場所
    "F5",  # 作業内容
    "B7",  # 事故概要及び略図
    "B23",  # 原因
    "B27",  # 再発防止対策
    "B32",  # リスク評価点
    "C33", "D33", "E33", "F33",  # 対策前の評価
    "C34", "D34", "E34", "F34",  # 対策後の評価
    "H32",  # 対応期限
    "B35",  # 相手情報
    "B40",  # その他
]

# 通知用で使わない領域（38行目以降）
NOTICE_LAST_ROW = 37


def make_notice(sample: Path, out: Path) -> None:
    wb = openpyxl.load_workbook(sample)
    for ws in list(wb.worksheets):
        if ws.title != NOTICE_SHEET:
            wb.remove(ws)
    ws = wb[NOTICE_SHEET]

    for rng in [r for r in ws.merged_cells.ranges if r.min_row > NOTICE_LAST_ROW]:
        ws.unmerge_cells(str(rng))
    for row in ws.iter_rows(min_row=NOTICE_LAST_ROW + 1, max_row=ws.max_row):
        for cell in row:
            cell.value = None
            cell.border = Border()
    ws.print_area = f"A1:I{NOTICE_LAST_ROW}"
    _clear_properties(wb)
    wb.save(out)


def make_detail(sample: Path, out: Path) -> None:
    wb = openpyxl.load_workbook(sample)
    detail = wb.worksheets[DETAIL_SHEET_INDEX]
    for ws in list(wb.worksheets):
        if ws is not detail:
            wb.remove(ws)
    detail.title = DETAIL_SHEET_TITLE
    for coord in DETAIL_VALUE_CELLS:
        detail[coord].value = None
    detail.print_area = "A1:J44"
    _clear_properties(wb)
    wb.save(out)


def _clear_properties(wb: openpyxl.Workbook) -> None:
    p = wb.properties
    p.creator = None
    p.lastModifiedBy = None
    p.title = None
    p.subject = None
    p.keywords = None
    p.description = None


def verify(path: Path) -> list[str]:
    """テンプレートに個別情報が残っていないか確認し、問題点を返す。"""
    problems = []
    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        if any(n.startswith(("xl/drawings/", "xl/media/")) for n in names):
            problems.append("図形または画像が残っている")
        core = z.read("docProps/core.xml").decode("utf-8")
        if "<dc:creator>" in core or "<cp:lastModifiedBy>" in core:
            problems.append("作成者情報が残っている")
    wb = openpyxl.load_workbook(path)
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for c in row:
                if c.value is not None:
                    print(f"  [{ws.title}] {c.coordinate}: {str(c.value)!r}")
    return problems


def main() -> int:
    if len(sys.argv) < 2:
        print(__doc__)
        return 1
    sample = Path(sys.argv[1])
    out_dir = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("templates")
    out_dir.mkdir(parents=True, exist_ok=True)

    outputs = [out_dir / "notice_template.xlsx", out_dir / "detail_template.xlsx"]
    make_notice(sample, outputs[0])
    make_detail(sample, outputs[1])

    ok = True
    for path in outputs:
        print(f"{path}: 残っている文字（見出しのみであること）")
        problems = verify(path)
        for p in problems:
            print(f"  NG: {p}")
        ok = ok and not problems
    return 0 if ok else 2


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
