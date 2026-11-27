"""確認画面: AIが作った文章を確認・修正し、通知用報告書（Excel）に出力する。

承認欄はAIが書かない。出力するのは、利用者がこの画面で確認した内容だけ。
"""

from __future__ import annotations

import os
from dataclasses import asdict
from datetime import datetime
from typing import TYPE_CHECKING

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QComboBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTextBrowser,
    QVBoxLayout,
    QWidget,
)

from accident_report.app.services import run_task
from accident_report.export.notice_excel import OUTPUT_DIR, WORK_CATEGORIES, NoticeForm, write_notice
from accident_report.interview.engine import InterviewEngine
from accident_report.storage import STATUS_EXPORTED, STATUS_REVIEW, delete_audio
from accident_report.writing.notice import check_draft, draft_to_form, generate_notice

if TYPE_CHECKING:
    from accident_report.app.main import MainWindow

SECTION = {"involved_persons": "氏名", "overview": "事故概要", "cause": "発生原因", "countermeasure": "対策案"}


def _lines(edit: QPlainTextEdit) -> list[str]:
    return [ln.strip().lstrip("・").strip() for ln in edit.toPlainText().splitlines() if ln.strip()]


class ReviewPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.case_id = 0
        self.reporter = ""
        self.engine: InterviewEngine | None = None
        self.saved: dict = {}

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        title = QLabel("報告書（通知用）の確認")
        title.setObjectName("title")
        outer.addWidget(title)
        self.busy = QLabel("")
        self.busy.setObjectName("chip")
        outer.addWidget(self.busy)
        self.issues = QLabel("")
        self.issues.setObjectName("warn")
        self.issues.setWordWrap(True)
        outer.addWidget(self.issues)

        split = QSplitter()
        outer.addWidget(split, 1)

        form_w = QWidget()
        form = QFormLayout(form_w)
        self.dt = QLineEdit()
        self.dt.setPlaceholderText("例: 2026年10月23日 14時ごろ")
        self.name = QLineEdit()
        self.name.setPlaceholderText("事故の当事者の氏名（複数なら「、」で区切る）")
        self.location = QLineEdit()
        self.category = QComboBox()
        self.category.addItems(WORK_CATEGORIES)
        self.category_other = QLineEdit()
        self.category_other.setPlaceholderText("「その他」のときの作業名")
        cat_row = QHBoxLayout()
        cat_row.addWidget(self.category)
        cat_row.addWidget(self.category_other, 1)
        self.overview = QPlainTextEdit()
        self.overview.setMinimumHeight(220)
        self.cause = QPlainTextEdit()
        self.cause.setMinimumHeight(90)
        self.countermeasure = QPlainTextEdit()
        self.countermeasure.setMinimumHeight(90)
        self.unresolved = QPlainTextEdit()
        self.unresolved.setMinimumHeight(70)
        form.addRow("日時", self.dt)
        form.addRow("氏名\n（事故当事者）", self.name)
        form.addRow("場所", self.location)
        form.addRow("作業区分", cat_row)
        form.addRow("事故概要\n（1行に1文）", self.overview)
        form.addRow("発生原因", self.cause)
        form.addRow("対策案", self.countermeasure)
        form.addRow("未確認事項", self.unresolved)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(form_w)
        split.addWidget(scroll)

        right = QWidget()
        rl = QVBoxLayout(right)
        rl.addWidget(QLabel("聞き取りの記録（文章の根拠）"))
        self.record = QTextBrowser()
        rl.addWidget(self.record)
        split.addWidget(right)
        split.setSizes([700, 400])

        row = QHBoxLayout()
        self.back_btn = QPushButton("聞き取りに戻る")
        self.back_btn.clicked.connect(self.back_to_interview)
        self.regen_btn = QPushButton("文章を作り直す")
        self.regen_btn.clicked.connect(self.generate)
        self.save_btn = QPushButton("保存して開始画面へ")
        self.save_btn.clicked.connect(self.save_and_leave)
        self.export_btn = QPushButton("Excelに出力する")
        self.export_btn.setObjectName("primary")
        self.export_btn.clicked.connect(self.export)
        for b in (self.back_btn, self.regen_btn, self.save_btn):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.export_btn)
        outer.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._elapsed = 0

    # ---- 開く ----
    def open(self, case_id: int, engine: InterviewEngine, reporter: str, saved: dict | None) -> None:
        self.case_id, self.engine, self.reporter = case_id, engine, reporter
        self.saved = saved or {}
        self._show_record()
        if self.saved.get("form"):
            self._fill(NoticeForm(**self.saved["form"]), self.saved.get("issues", []))
        else:
            self.generate()

    def _show_record(self) -> None:
        html = []
        for a in self.engine.answers:
            html.append(f"<p><b>{a.answer_id}</b>　<span style='color:#555'>{a.question_text}</span><br>{a.text}</p>")
        self.record.setHtml("".join(html))

    # ---- 文章の生成 ----
    def generate(self) -> None:
        eng, reporter = self.engine, self.reporter
        writer = self.win.services.client("writer_model")
        self._set_busy(True)

        def work():
            draft = generate_notice(writer, eng, reporter)
            issues = check_draft(draft, eng, reporter)
            return draft, issues, draft_to_form(eng, draft, reporter)

        run_task(work, self._generated, self._generate_failed)

    def _generated(self, result) -> None:
        draft, issues, form = result
        self._set_busy(False)
        issue_texts = [f"{SECTION.get(i.section, i.section)}「{i.text[:30]}…」: {i.problem}" for i in issues]
        self.saved = {"draft": draft.model_dump(mode="json"), "issues": issue_texts, "form": asdict(form)}
        self.win.store.save_case(self.case_id, draft=self.saved, status=STATUS_REVIEW)
        self._fill(form, issue_texts)

    def _generate_failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.warning(self, "文章の作成", "AIで文章を作れませんでした。聞き取った内容を入れておくので、"
                            f"直接入力してください。\n{message}")
        eng = self.engine
        val = lambda i: eng.states[i].value or "" if i in eng.states else ""  # noqa: E731
        form = NoticeForm(datetime_text=val("datetime"), name="", location=val("location"),
                          overview=[a.text for a in eng.answers if a.question_item_id not in ("cause", "countermeasure")],
                          cause=[val("cause")] if val("cause") else [],
                          countermeasure=[val("countermeasure")] if val("countermeasure") else [],
                          unresolved=[f"{lbl}（{st.value}）" for lbl, st in eng.unresolved()])
        self._fill(form, ["AIを使わずに作った下書きです。文章を整えてから出力してください。"])

    def _fill(self, form: NoticeForm, issues: list[str]) -> None:
        self.dt.setText(form.datetime_text)
        self.name.setText(form.name)
        self.location.setText(form.location)
        self.category.setCurrentText(form.work_category or "その他")
        self.category_other.setText(form.work_category_other)
        self.overview.setPlainText("\n".join(form.overview))
        self.cause.setPlainText("\n".join(form.cause))
        self.countermeasure.setPlainText("\n".join(form.countermeasure))
        self.unresolved.setPlainText("\n".join(form.unresolved))
        self.issues.setText(("確認してください: " + "／".join(issues)) if issues else "")

    def collect(self) -> NoticeForm:
        return NoticeForm(
            datetime_text=self.dt.text().strip(), name=self.name.text().strip(),
            location=self.location.text().strip(), work_category=self.category.currentText(),
            work_category_other=self.category_other.text().strip(),
            overview=_lines(self.overview), cause=_lines(self.cause),
            countermeasure=_lines(self.countermeasure), unresolved=_lines(self.unresolved))

    def _save_form(self, **kw) -> None:
        self.saved["form"] = asdict(self.collect())
        self.win.store.save_case(self.case_id, draft=self.saved, **kw)

    # ---- ボタン ----
    def back_to_interview(self) -> None:
        self._save_form(status="聞き取り中")
        self.win.resume_interview(self.case_id)

    def save_and_leave(self) -> None:
        self._save_form()
        self.win.show_start()

    def export(self) -> None:
        form = self.collect()
        missing = [n for n, v in (("日時", form.datetime_text), ("氏名", form.name), ("場所", form.location), ("事故概要", form.overview)) if not v]
        if missing and QMessageBox.question(
                self, "出力", f"{'・'.join(missing)} が空欄です。このまま出力しますか？") != QMessageBox.StandardButton.Yes:
            return
        path = OUTPUT_DIR / f"事故報告_通知用_{datetime.now():%Y%m%d-%H%M}_{self.case_id:05d}.xlsx"
        try:
            path = write_notice(form, path)
        except PermissionError:
            QMessageBox.warning(self, "出力", "同じ名前のファイルがExcelで開かれています。閉じてからもう一度押してください。")
            return
        except FileNotFoundError as e:
            QMessageBox.warning(self, "出力", str(e))
            return
        self._save_form(status=STATUS_EXPORTED, output_path=str(path))
        note = ""
        if self.win.store.setting("delete_audio_after_export") == "1" and delete_audio(self.case_id):
            note = "\n録音は設定に従って削除しました。"
        box = QMessageBox(self)
        box.setWindowTitle("出力しました")
        box.setText(f"報告書を出力しました。\n{path}{note}")
        open_btn = box.addButton("Excelで開く", QMessageBox.ButtonRole.AcceptRole)
        folder_btn = box.addButton("フォルダーを開く", QMessageBox.ButtonRole.ActionRole)
        box.addButton("閉じる", QMessageBox.ButtonRole.RejectRole)
        box.exec()
        if box.clickedButton() is open_btn:
            os.startfile(path)  # noqa: S606 - 同じPCのExcelで開くだけ
        elif box.clickedButton() is folder_btn:
            os.startfile(path.parent)  # noqa: S606

    # ---- 共通 ----
    def _set_busy(self, busy: bool) -> None:
        for w in (self.back_btn, self.regen_btn, self.save_btn, self.export_btn):
            w.setEnabled(not busy)
        self._elapsed = 0
        if busy:
            self.busy.setText("AIが報告書の文章を作っています（2〜3分かかります）…")
            self._timer.start()
        else:
            self.busy.setText("")
            self._timer.stop()

    def _tick(self) -> None:
        self._elapsed += 1
        self.busy.setText(f"AIが報告書の文章を作っています（2〜3分かかります）… {self._elapsed}秒")


