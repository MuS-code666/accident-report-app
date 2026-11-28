"""対策の進み具合の一覧（全報告の再発防止対策）。状態と完了日はここで更新できる。"""

from __future__ import annotations

import re
from datetime import date

from PySide6.QtGui import QColor
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from accident_report.dates import normalize_datetime
from accident_report.models.detail import MEASURE_STATUSES, DetailReport
from accident_report.storage import Store


def due_date(text: str, today: date | None = None) -> date | None:
    """「2026年12月15日」などの期限を日付にする。読めなければ None。"""
    norm = normalize_datetime(text, today or date.today()) if text.strip() else None
    m = re.match(r"(\d{4})年(\d{1,2})月(\d{1,2})日", norm or "")
    return date(int(m.group(1)), int(m.group(2)), int(m.group(3))) if m else None


def is_overdue(due: str, status: str, today: date | None = None) -> bool:
    today = today or date.today()
    d = due_date(due, today)
    return status != "完了" and d is not None and d < today


class MeasuresDialog(QDialog):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("再発防止対策の進み具合")
        self.resize(1100, 620)
        lay = QVBoxLayout(self)
        note = QLabel("すべての詳細報告の対策です。期限を過ぎて完了していない対策は赤く表示します。"
                      "状態と完了日はここで直せます（閉じるときに保存します）。")
        note.setWordWrap(True)
        lay.addWidget(note)
        self.hide_done = QCheckBox("完了した対策を隠す")
        self.hide_done.setChecked(True)
        self.hide_done.toggled.connect(self.refresh)
        lay.addWidget(self.hide_done)
        self.table = QTableWidget(0, 6)
        self.table.setHorizontalHeaderLabels(["報告", "対策", "担当", "期限", "状態", "完了日"])
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        lay.addWidget(self.table, 1)
        self.summary = QLabel("")
        lay.addWidget(self.summary)
        row = QHBoxLayout()
        row.addStretch()
        close = QPushButton("保存して閉じる")
        close.clicked.connect(self.accept)
        row.addWidget(close)
        lay.addLayout(row)
        self.reports: dict[int, DetailReport] = {}
        self.rows: list[tuple[int, int]] = []  # 表の行 → (報告ID, 対策の番号)
        self.titles: dict[int, str] = {}
        for c in store.list_cases():
            if c.detail:
                self.reports[c.id] = DetailReport.model_validate(c.detail)
                self.titles[c.id] = f"{c.created_at:%m/%d} {c.title or c.reporter}"
        self.refresh()

    def _pull(self) -> None:
        """表の編集内容をデータに戻す。"""
        for r, (cid, i) in enumerate(self.rows):
            m = self.reports[cid].measures[i]
            m.status = self.table.cellWidget(r, 4).currentText()
            item = self.table.item(r, 5)
            m.done_on = item.text().strip() if item else ""

    def refresh(self) -> None:
        if self.rows:
            self._pull()
        self.table.setRowCount(0)
        self.rows = []
        total = overdue = done = 0
        for cid, rep in self.reports.items():
            for i, m in enumerate(rep.measures):
                total += 1
                done += m.status == "完了"
                late = is_overdue(m.due, m.status)
                overdue += late
                if self.hide_done.isChecked() and m.status == "完了":
                    continue
                r = self.table.rowCount()
                self.table.insertRow(r)
                for col, v in ((0, self.titles[cid]), (1, m.text), (2, m.owner), (3, m.due), (5, m.done_on)):
                    item = QTableWidgetItem(v)
                    if col != 5:
                        item.setFlags(item.flags() & ~item.flags().ItemIsEditable)
                    if late:
                        item.setBackground(QColor("#ffd7d7"))
                    self.table.setItem(r, col, item)
                combo = QComboBox()
                combo.addItems(MEASURE_STATUSES)
                combo.setCurrentText(m.status)
                self.table.setCellWidget(r, 4, combo)
                self.rows.append((cid, i))
        self.summary.setText(f"対策 {total} 件（完了 {done} 件・期限切れ {overdue} 件）")

    def done(self, r: int) -> None:  # 閉じるときに保存
        self._pull()
        for cid, rep in self.reports.items():
            self.store.save_case(cid, detail=rep.model_dump(mode="json"))
        super().done(r)
