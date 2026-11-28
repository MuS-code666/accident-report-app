"""詳細報告画面（管理監督者用）: 原因分析・再発防止対策・リスク評価を入力し、詳細報告書を出力する。

通知用の確認済みの内容を引き継ぐ。AIの候補は「AI案」として表に足すだけで、採否は管理監督者が決める。
リスク評価は会社の基準で手入力する（計算しない）。承認欄は扱わない。
"""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTableWidget,
    QTableWidgetItem,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from accident_report.app.services import run_task
from accident_report.export.detail_excel import write_detail
from accident_report.export.notice_excel import OUTPUT_DIR
from accident_report.interview.engine import InterviewEngine
from accident_report.models.detail import (
    CAUSE_CATEGORIES,
    MEASURE_STATUSES,
    CauseItem,
    DetailReport,
    Measure,
    RiskRow,
)
from accident_report.writing.detail import suggest

if TYPE_CHECKING:
    from accident_report.app.main import MainWindow


def _lines(edit: QPlainTextEdit) -> list[str]:
    return [ln.strip().lstrip("・").strip() for ln in edit.toPlainText().splitlines() if ln.strip()]


def _combo(items: list[str], current: str) -> QComboBox:
    c = QComboBox()
    c.addItems(items)
    c.setCurrentText(current)
    return c


class DetailPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.case_id = 0
        self.engine: InterviewEngine | None = None
        self.report = DetailReport()

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        title = QLabel("詳細報告（管理監督者用）")
        title.setObjectName("title")
        outer.addWidget(title)
        self.busy = QLabel("")
        self.busy.setObjectName("chip")
        outer.addWidget(self.busy)

        split = QSplitter()
        outer.addWidget(split, 1)
        body = QWidget()
        lay = QVBoxLayout(body)

        basic = QGroupBox("基本情報（通知用から引き継ぎ）")
        form = QFormLayout(basic)
        self.dt, self.name, self.location, self.work = QLineEdit(), QLineEdit(), QLineEdit(), QLineEdit()
        self.overview = QPlainTextEdit()
        self.overview.setMinimumHeight(140)
        form.addRow("日時", self.dt)
        form.addRow("氏名（事故当事者）", self.name)
        form.addRow("場所", self.location)
        form.addRow("作業内容", self.work)
        form.addRow("事故概要（1行に1文）", self.overview)
        self.fig_status = QLabel("")
        self.fig_btn = QPushButton("事故状況図を作る・直す")
        self.fig_btn.clicked.connect(self.open_diagram)
        fig_row = QHBoxLayout()
        fig_row.addWidget(self.fig_status, 1)
        fig_row.addWidget(self.fig_btn)
        form.addRow("事故状況図", fig_row)
        lay.addWidget(basic)

        self.reference = QLabel("")
        self.reference.setWordWrap(True)
        self.reference.setObjectName("muted")
        lay.addWidget(self.reference)

        cause_box = QGroupBox("原因")
        cl = QVBoxLayout(cause_box)
        self.causes = QTableWidget(0, 3)
        self.causes.setHorizontalHeaderLabels(["分類", "原因", "出どころ"])
        self.causes.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        self.causes.setColumnWidth(0, 170)  # 「作業方法・環境」が切れない幅
        self.causes.setMinimumHeight(170)
        cl.addWidget(self.causes)
        row = QHBoxLayout()
        self.ai_btn = QPushButton("AIで原因と対策の候補を出す")
        self.ai_btn.clicked.connect(self.ask_ai)
        add_c = QPushButton("原因を追加")
        add_c.clicked.connect(lambda: self._add_cause(CauseItem()))
        del_c = QPushButton("選んだ原因を削除")
        del_c.clicked.connect(lambda: self._delete_rows(self.causes))
        for b in (self.ai_btn, add_c, del_c):
            row.addWidget(b)
        row.addStretch()
        cl.addLayout(row)
        self.ai_notes = QLabel("")
        self.ai_notes.setWordWrap(True)
        self.ai_notes.setObjectName("muted")
        cl.addWidget(self.ai_notes)
        lay.addWidget(cause_box)

        m_box = QGroupBox("再発防止対策")
        ml = QVBoxLayout(m_box)
        self.measures = QTableWidget(0, 6)
        self.measures.setHorizontalHeaderLabels(["対策", "担当", "期限", "状態", "完了日", "出どころ"])
        self.measures.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.measures.setMinimumHeight(170)
        ml.addWidget(self.measures)
        row = QHBoxLayout()
        add_m = QPushButton("対策を追加")
        add_m.clicked.connect(lambda: self._add_measure(Measure()))
        del_m = QPushButton("選んだ対策を削除")
        del_m.clicked.connect(lambda: self._delete_rows(self.measures))
        row.addWidget(add_m)
        row.addWidget(del_m)
        row.addStretch()
        ml.addLayout(row)
        lay.addWidget(m_box)

        r_box = QGroupBox("リスク評価（会社の基準で入力。アプリは計算しません）")
        grid = QGridLayout(r_box)
        self.risk_score = QLineEdit()
        grid.addWidget(QLabel("リスク評価点"), 0, 0)
        grid.addWidget(self.risk_score, 0, 1)
        heads = ["重篤度", "可能性", "頻度", "リスクレベル"]
        for j, h in enumerate(heads):
            grid.addWidget(QLabel(h), 1, j + 1)
        self.risk = {}
        for i, key in enumerate(("before", "after")):
            grid.addWidget(QLabel("対策前" if key == "before" else "対策後"), i + 2, 0)
            for j in range(4):
                e = QLineEdit()
                e.setMaximumWidth(110)
                grid.addWidget(e, i + 2, j + 1)
                self.risk[(key, j)] = e
        self.risk_due = QLineEdit()
        self.risk_due.setPlaceholderText("例: 2026年12月15日")
        grid.addWidget(QLabel("対応期限"), 4, 0)
        grid.addWidget(self.risk_due, 4, 1, 1, 3)
        lay.addWidget(r_box)

        o_box = QGroupBox("その他")
        of = QFormLayout(o_box)
        self.counterpart, self.other = QPlainTextEdit(), QPlainTextEdit()
        self.counterpart.setMaximumHeight(80)
        self.other.setMaximumHeight(80)
        of.addRow("相手情報", self.counterpart)
        of.addRow("その他", self.other)
        lay.addWidget(o_box)

        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(body)
        split.addWidget(scroll)
        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("聞き取りの記録（原因の根拠）"))
        self.record = QTextBrowser()
        rl.addWidget(self.record)
        split.addWidget(right)
        split.setSizes([780, 360])

        row = QHBoxLayout()
        self.save_btn = QPushButton("保存して開始画面へ")
        self.save_btn.clicked.connect(self.save_and_leave)
        self.export_btn = QPushButton("詳細報告をExcelに出力する")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self.export)
        row.addWidget(self.save_btn)
        row.addStretch()
        row.addWidget(self.export_btn)
        outer.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._elapsed = 0

    # ---- 開く ----
    def open(self, case_id: int, engine: InterviewEngine | None, report: DetailReport,
             notice_form: dict | None, diagram: dict | None) -> None:
        self.case_id, self.engine, self.report = case_id, engine, report
        self.diagram = diagram or {}
        self.dt.setText(report.datetime_text)
        self.name.setText(report.name)
        self.location.setText(report.location)
        self.work.setText(report.work_content)
        self.overview.setPlainText("\n".join(report.overview))
        self.causes.setRowCount(0)
        self.measures.setRowCount(0)
        for c in report.causes:
            self._add_cause(c)
        for m in report.measures:
            self._add_measure(m)
        self.risk_score.setText(report.risk_score)
        for key, row in (("before", report.risk_before), ("after", report.risk_after)):
            for j, v in enumerate((row.severity, row.likelihood, row.frequency, row.level)):
                self.risk[(key, j)].setText(v)
        self.risk_due.setText(report.risk_due)
        self.counterpart.setPlainText(report.counterpart)
        self.other.setPlainText(report.other)
        self.ai_notes.setText("")
        f = notice_form or {}
        ref = []
        if f.get("cause"):
            ref.append("報告者が考えた原因: " + "／".join(f["cause"]))
        if f.get("countermeasure"):
            ref.append("報告者が考えた対策: " + "／".join(f["countermeasure"]))
        self.reference.setText("参考（通知用より）: " + "　".join(ref) if ref else "")
        if engine is not None:
            self.record.setHtml("".join(f"<p><b>{a.answer_id}</b>　<span style='color:#555'>{a.question_text}</span>"
                                        f"<br>{a.text}</p>" for a in engine.answers))
        else:
            self.record.setHtml("<p>聞き取りの記録はありません（詳細報告から新しく作った報告です）。</p>")
        self.ai_btn.setEnabled(engine is not None and bool(engine.answers))
        has_fig = bool(self.diagram.get("accepted") and self.diagram.get("main_png")
                       and Path(self.diagram["main_png"]).exists())
        self.fig_status.setText("通知用で作った図を貼ります。" if has_fig else "図はありません（略図欄は手書き用に空けます）。")
        self.fig_btn.setEnabled(engine is not None and bool(engine.answers))

    # ---- 表 ----
    def _add_cause(self, c: CauseItem) -> None:
        r = self.causes.rowCount()
        self.causes.insertRow(r)
        self.causes.setCellWidget(r, 0, _combo(CAUSE_CATEGORIES, c.category))
        self.causes.setItem(r, 1, QTableWidgetItem(c.text))
        src = QTableWidgetItem(c.source + (f"（根拠 {', '.join(c.evidence)}）" if c.evidence else ""))
        src.setData(256, c.model_dump(mode="json"))  # 元のデータ（根拠など）を保持
        self.causes.setItem(r, 2, src)

    def _add_measure(self, m: Measure) -> None:
        r = self.measures.rowCount()
        self.measures.insertRow(r)
        for col, v in ((0, m.text), (1, m.owner), (2, m.due), (4, m.done_on), (5, m.source)):
            self.measures.setItem(r, col, QTableWidgetItem(v))
        self.measures.setCellWidget(r, 3, _combo(MEASURE_STATUSES, m.status))

    @staticmethod
    def _delete_rows(table: QTableWidget) -> None:
        for r in sorted({i.row() for i in table.selectedIndexes()}, reverse=True):
            table.removeRow(r)

    @staticmethod
    def _text(table: QTableWidget, r: int, c: int) -> str:
        item = table.item(r, c)
        return item.text().strip() if item else ""

    def collect(self) -> DetailReport:
        causes = []
        for r in range(self.causes.rowCount()):
            text = self._text(self.causes, r, 1)
            if not text:
                continue
            src = self.causes.item(r, 2)
            orig = (src.data(256) if src else None) or {}
            causes.append(CauseItem(category=self.causes.cellWidget(r, 0).currentText(), text=text,
                                    evidence=orig.get("evidence", []), source=orig.get("source", "手入力")))
        measures = []
        for r in range(self.measures.rowCount()):
            text = self._text(self.measures, r, 0)
            if not text:
                continue
            src = self._text(self.measures, r, 5)
            measures.append(Measure(text=text, owner=self._text(self.measures, r, 1), due=self._text(self.measures, r, 2),
                                    status=self.measures.cellWidget(r, 3).currentText(),
                                    done_on=self._text(self.measures, r, 4),
                                    source="AI案" if src == "AI案" else "手入力"))
        risk = {k: RiskRow(**{f: self.risk[(k, j)].text().strip()
                              for j, f in enumerate(("severity", "likelihood", "frequency", "level"))})
                for k in ("before", "after")}
        return self.report.model_copy(update=dict(
            datetime_text=self.dt.text().strip(), name=self.name.text().strip(),
            location=self.location.text().strip(), work_content=self.work.text().strip(),
            overview=_lines(self.overview), causes=causes, measures=measures,
            risk_score=self.risk_score.text().strip(), risk_before=risk["before"], risk_after=risk["after"],
            risk_due=self.risk_due.text().strip(), counterpart=self.counterpart.toPlainText().strip(),
            other=self.other.toPlainText().strip()))

    def _save(self, **kw) -> DetailReport:
        self.report = self.collect()
        self.win.store.save_case(self.case_id, detail=self.report.model_dump(mode="json"), **kw)
        return self.report

    # ---- AI の候補 ----
    def ask_ai(self) -> None:
        report = self.collect()
        client, eng = self.win.services.client("writer_model"), self.engine
        self._set_busy(True)
        run_task(lambda: suggest(client, eng, report), self._suggested, self._failed)

    def _suggested(self, result) -> None:
        causes, measures, notes = result
        self._set_busy(False)
        for c in causes:
            self._add_cause(c)
        for m in measures:
            self._add_measure(m)
        msg = f"AIの候補を {len(causes)} 件の原因、{len(measures)} 件の対策として追加しました。内容を確かめ、不要な行は削除してください。"
        if notes:
            msg += "\n" + "\n".join(notes)
        self.ai_notes.setText(msg)
        self._save()

    def _failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.warning(self, "AI", f"候補を作れませんでした。手で入力してください。\n{message}")

    # ---- ボタン ----
    def open_diagram(self) -> None:
        self._save()
        self.win.show_diagram(self.case_id, return_to="detail")

    def save_and_leave(self) -> None:
        self._save()
        self.win.show_start()

    def export(self) -> None:
        report = self.collect()
        d = self.diagram
        figure = Path(d["main_png"]) if d.get("accepted") and d.get("main_png") and Path(d["main_png"]).exists() else None
        extra = Path(d["extra_png"]) if figure and d.get("extra_png") and Path(d["extra_png"]).exists() else None
        path = OUTPUT_DIR / f"事故報告_詳細_{datetime.now():%Y%m%d-%H%M}_{self.case_id:05d}.xlsx"
        try:
            path = write_detail(report, path, figure=figure, extra_figure=extra)
        except PermissionError:
            QMessageBox.warning(self, "出力", "同じ名前のファイルがExcelで開かれています。閉じてからもう一度押してください。")
            return
        except FileNotFoundError as e:
            QMessageBox.warning(self, "出力", str(e))
            return
        self._save(detail_output_path=str(path))
        box = QMessageBox(self)
        box.setWindowTitle("出力しました")
        box.setText(f"詳細報告を出力しました。\n{path}\n\n承認欄は空欄です。印刷して承認を受けてください。")
        open_btn = box.addButton("Excelで開く", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("フォルダーを開く", QMessageBox.ButtonRole.ActionRole)
        box.addButton("閉じる", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            os.startfile(path)  # noqa: S606
        elif box.clickedButton() is folder_btn:
            os.startfile(path.parent)  # noqa: S606

    def _set_busy(self, busy: bool) -> None:
        for w in (self.ai_btn, self.save_btn, self.export_btn, self.fig_btn):
            w.setEnabled(not busy)
        self._elapsed = 0
        if busy:
            self.busy.setText("AIが原因と対策の候補を考えています（1〜3分かかります）…")
            self._timer.start()
        else:
            self.busy.setText("")
            self._timer.stop()
            has_record = self.engine is not None and bool(self.engine.answers)
            self.ai_btn.setEnabled(has_record)
            self.fig_btn.setEnabled(has_record)

    def _tick(self) -> None:
        self._elapsed += 1
        self.busy.setText(f"AIが原因と対策の候補を考えています（1〜3分かかります）… {self._elapsed}秒")
