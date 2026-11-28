"""作図画面: AIが整理した位置関係から図を描き、足りない情報を選択式で聞いて直す。

回答はAIを通さずに図のデータへ直接反映する。最後に利用者が「この図を使う」を押した図だけを報告書に貼る。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QScrollArea,
    QVBoxLayout,
    QWidget,
)

from accident_report.app.services import run_task
from accident_report.diagram import service
from accident_report.diagram.questions import DIRECTIONS
from accident_report.interview.engine import InterviewEngine
from accident_report.storage import diagram_dir

if TYPE_CHECKING:
    from accident_report.app.main import MainWindow

# 8方向のボタンの並び（上が重機・車両の前）
COMPASS = {"左前": (0, 0), "前": (0, 1), "右前": (0, 2), "左": (1, 0), "右": (1, 2),
           "左後": (2, 0), "後": (2, 1), "右後": (2, 2)}


class DiagramPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.case_id = 0
        self.engine: InterviewEngine | None = None
        self.state: service.DiagramState | None = None
        self.question = None
        self.return_to = "review"  # 図を作り終えたら戻る画面（review / detail）

        outer = QVBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        title = QLabel("事故状況図")
        title.setObjectName("title")
        outer.addWidget(title)
        self.busy = QLabel("")
        self.busy.setObjectName("chip")
        outer.addWidget(self.busy)

        body = QHBoxLayout()
        outer.addLayout(body, 1)
        self.image = QLabel("")
        self.image.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.image.setStyleSheet("background: white; border: 1px solid #ccc;")
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setWidget(self.image)
        body.addWidget(scroll, 3)

        side = QVBoxLayout()
        body.addLayout(side, 2)
        self.q_count = QLabel("")
        self.q_count.setObjectName("muted")
        side.addWidget(self.q_count)
        self.q_text = QLabel("")
        self.q_text.setObjectName("question")
        self.q_text.setWordWrap(True)
        side.addWidget(self.q_text)
        self.choices = QWidget()
        self.choices_layout = QGridLayout(self.choices)
        side.addWidget(self.choices)
        self.notes = QLabel("")
        self.notes.setWordWrap(True)
        self.notes.setObjectName("muted")
        side.addWidget(self.notes)
        side.addStretch()

        row = QHBoxLayout()
        self.back_btn = QPushButton("文章の確認に戻る（図は保存）")
        self.back_btn.clicked.connect(self.back)
        self.regen_btn = QPushButton("図を最初から作り直す")
        self.regen_btn.clicked.connect(self.generate)
        self.edit_btn = QPushButton("図を手で直す")
        self.edit_btn.clicked.connect(self.edit)
        self.skip_btn = QPushButton("図を報告書に使わない")
        self.skip_btn.clicked.connect(self.skip)
        self.use_btn = QPushButton("この図を報告書に使う")
        self.use_btn.setObjectName("primary")
        self.use_btn.clicked.connect(self.accept)
        for b in (self.back_btn, self.regen_btn, self.edit_btn, self.skip_btn):
            row.addWidget(b)
        row.addStretch()
        row.addWidget(self.use_btn)
        outer.addLayout(row)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._tick)
        self._elapsed = 0

    # ---- 開く ----
    def open(self, case_id: int, engine: InterviewEngine, saved: dict | None, return_to: str = "review") -> None:
        self.case_id, self.engine, self.return_to = case_id, engine, return_to
        if saved and saved.get("spec"):
            self.state = service.DiagramState.model_validate(saved)
            self._redraw()
        else:
            self.state = None
            self.image.clear()
            self.generate()

    def generate(self) -> None:
        eng = self.engine
        client = self.win.services.client("writer_model")
        self._set_busy(True)
        run_task(lambda: service.create_state(client, eng), self._created, self._failed)

    def _created(self, state: service.DiagramState) -> None:
        self._set_busy(False)
        self.state = state
        self._redraw()

    def _failed(self, message: str) -> None:
        self._set_busy(False)
        QMessageBox.warning(self, "作図", "AIで図を作れませんでした。もう一度試すか、図を使わずに進めてください。"
                            f"\n{message}")
        if self.state is None:
            self._show_question(None)

    # ---- 図と質問 ----
    def _redraw(self) -> None:
        st = self.state
        try:
            main, _extra, warnings = service.render(st, diagram_dir(self.case_id), "diagram")
        except Exception as e:  # noqa: BLE001
            QMessageBox.warning(self, "作図", f"図を描けませんでした。\n{e}")
            return
        pix = QPixmap(str(main))
        self.image.setPixmap(pix.scaledToWidth(min(pix.width(), 900), Qt.TransformationMode.SmoothTransformation))
        notes = list(warnings)
        notes += [u for u in st.spec.unknowns if not u.startswith("［点検］")]
        self.notes.setText(("気になる点:\n・" + "\n・".join(notes)) if notes else "")
        self._save()
        qs = service.pending_questions(st)
        self._show_question(qs[0] if qs else None, len(st.answers) + 1, len(st.answers) + len(qs))

    def _clear_choices(self) -> None:
        while self.choices_layout.count():
            w = self.choices_layout.takeAt(0).widget()
            if w:
                w.deleteLater()

    def _show_question(self, q, n: int = 0, total: int = 0) -> None:
        self.question = q
        self._clear_choices()
        if q is None:
            self.q_count.setText("")
            self.q_text.setText("追加の質問は終わりました。図を確認して、よければ「この図を報告書に使う」を押してください。"
                                if self.state else "")
            return
        self.q_count.setText(f"図のための質問 {n} / {total}")
        self.q_text.setText(q.text)
        directional = set(DIRECTIONS) <= set(q.choices)
        if directional:
            anchor = self.state.spec.entity(self.state.spec.scenes[0].anchor)
            center = QLabel(f"{anchor.label if anchor else '重機'}\n（上が前）")
            center.setAlignment(Qt.AlignmentFlag.AlignCenter)
            center.setStyleSheet("border: 2px solid #1f4e79; border-radius: 6px; padding: 8px; font-weight: bold;")
            self.choices_layout.addWidget(center, 1, 1)
        for i, choice in enumerate(q.choices):
            b = QPushButton(choice)
            b.setMinimumHeight(52)
            b.clicked.connect(lambda _=False, c=choice: self.choose(c))
            if directional and choice in COMPASS:
                r, c = COMPASS[choice]
                self.choices_layout.addWidget(b, r, c)
            elif directional:
                self.choices_layout.addWidget(b, 3, 0, 1, 3)
            else:
                self.choices_layout.addWidget(b, i, 0)

    def choose(self, choice: str) -> None:
        if self.question is None:
            return
        service.answer(self.state, self.question, choice)
        self._redraw()

    # ---- ボタン ----
    def edit(self) -> None:
        if self.state is None:
            return
        from accident_report.app.diagram_editor import DiagramEditor

        if DiagramEditor(self.state, self).exec():
            self._redraw()

    def accept(self) -> None:
        if self.state is None:
            return
        self.state.accepted = True
        self._save()
        self._leave()

    def skip(self) -> None:
        if self.state is not None:
            self.state.accepted = False
            self._save()
        self._leave()

    def back(self) -> None:
        if self.state is not None:
            self._save()
        self._leave()

    def _leave(self) -> None:
        if self.return_to == "detail":
            self.win.show_detail(self.case_id)
        else:
            self.win.show_review(self.case_id)

    def _save(self) -> None:
        self.win.store.save_case(self.case_id, diagram=self.state.model_dump(mode="json"))

    def _set_busy(self, busy: bool) -> None:
        for w in (self.back_btn, self.regen_btn, self.edit_btn, self.skip_btn, self.use_btn, self.choices):
            w.setEnabled(not busy)
        self._elapsed = 0
        if busy:
            self.busy.setText("AIが事故の位置関係を整理しています（2〜3分かかります）…")
            self._timer.start()
        else:
            self.busy.setText("")
            self._timer.stop()

    def _tick(self) -> None:
        self._elapsed += 1
        self.busy.setText(f"AIが事故の位置関係を整理しています（2〜3分かかります）… {self._elapsed}秒")


