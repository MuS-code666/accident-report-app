"""聞き取り画面: 質問の読み上げ → 押して話す → 認識結果の確認・修正 → 回答。

1回答ごとに自動保存する。AIが使えないときも定型の質問で続けられる。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import numpy as np
from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from accident_report.app.services import Recorder, run_task
from accident_report.interview.engine import InterviewEngine, Question
from accident_report.models.interview import ItemStatus
from accident_report.speech.asr import write_wav
from accident_report.storage import audio_dir

if TYPE_CHECKING:
    from accident_report.app.main import MainWindow

STATUS_MARK = {
    ItemStatus.UNCONFIRMED: "　", ItemStatus.ANSWERED: "✔", ItemStatus.INSUFFICIENT: "△",
    ItemStatus.CONFLICT: "！", ItemStatus.UNKNOWN: "？", ItemStatus.NOT_APPLICABLE: "－",
}


class InterviewPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        self.case_id = 0
        self.reporter = ""
        self.engine: InterviewEngine | None = None
        self.recorder = Recorder()
        self._audio: np.ndarray | None = None
        self._raw: str | None = None

        outer = QHBoxLayout(self)
        outer.setContentsMargins(24, 16, 24, 16)
        main = QVBoxLayout()
        outer.addLayout(main, 3)

        self.progress = QLabel("")
        self.progress.setObjectName("muted")
        main.addWidget(self.progress)
        self.chip = QLabel("")
        self.chip.setObjectName("chip")
        main.addWidget(self.chip)
        self.question = QLabel("")
        self.question.setObjectName("question")
        self.question.setWordWrap(True)
        main.addWidget(self.question)
        reread = QPushButton("質問をもう一度読む")
        reread.clicked.connect(lambda: self._speak(self.question.text()))
        row0 = QHBoxLayout()
        row0.addWidget(reread)
        row0.addStretch()
        main.addLayout(row0)

        self.record_btn = QPushButton()
        self.record_btn.setObjectName("record")
        self.record_btn.clicked.connect(self.toggle_record)
        main.addWidget(self.record_btn)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(10)
        main.addWidget(self.level)

        self.answer = QPlainTextEdit()
        self.answer.setObjectName("answer")
        self.answer.setPlaceholderText("話した内容がここに表示されます。間違いがあれば直してください。キーボードで入力することもできます。")
        main.addWidget(self.answer, 1)

        self.busy_label = QLabel("")
        self.busy_label.setObjectName("chip")
        main.addWidget(self.busy_label)

        self.submit_btn = QPushButton("この内容で回答する")
        self.submit_btn.setObjectName("primary")
        self.submit_btn.clicked.connect(self.submit)
        main.addWidget(self.submit_btn)

        row = QHBoxLayout()
        self.dk_btn = QPushButton("分からない")
        self.na_btn = QPushButton("該当なし")
        self.later_btn = QPushButton("後で答える")
        self.dk_btn.clicked.connect(lambda: self.press("dont_know"))
        self.na_btn.clicked.connect(lambda: self.press("not_applicable"))
        self.later_btn.clicked.connect(lambda: self.press("later"))
        for b in (self.dk_btn, self.na_btn, self.later_btn):
            row.addWidget(b)
        main.addLayout(row)

        row2 = QHBoxLayout()
        self.pause_btn = QPushButton("中断して保存（あとで再開）")
        self.pause_btn.clicked.connect(self.pause)
        self.finish_btn = QPushButton("聞き取りを終えて文章を作る")
        self.finish_btn.clicked.connect(self.finish_early)
        row2.addWidget(self.pause_btn)
        row2.addStretch()
        row2.addWidget(self.finish_btn)
        main.addLayout(row2)

        side = QVBoxLayout()
        side.addWidget(QLabel("聞き取りの状況"))
        self.items = QListWidget()
        side.addWidget(self.items, 1)
        legend = QLabel("✔回答済み　△情報不足　！矛盾あり　？分からない　－該当なし")
        legend.setWordWrap(True)
        legend.setObjectName("muted")
        side.addWidget(legend)
        outer.addLayout(side, 1)

        self._timer = QTimer(self)
        self._timer.setInterval(100)
        self._timer.timeout.connect(self._tick)
        self._busy_since = 0
        self._busy_text = ""
        self._set_record_button(False)

    # ---- 開く ----
    def open(self, case_id: int, engine: InterviewEngine, reporter: str) -> None:
        self.case_id, self.engine, self.reporter = case_id, engine, reporter
        q = engine.current or engine.next_question()
        self._save()
        self.show_question(q)
        run_task(lambda: self.win.services.client("extract_model").warm_up())  # AIを先に読み込んでおく

    def show_question(self, q: Question | None) -> None:
        if q is None:
            self._go_review()
            return
        eng = self.engine
        done, total = eng.progress()
        self.progress.setText(f"必須の項目 {done} / {total} 件　（回答 {len(eng.answers)} 件）")
        label = "最初の質問" if q.item_id is None else eng.qset.item(q.item_id).label
        self.chip.setText({"conflict": f"確認：{label}", "deepen": f"詳しく：{label}"}.get(q.kind, label))
        self.question.setText(q.text)
        self.answer.clear()
        self._audio, self._raw = None, None
        opening = q.item_id is None
        for b in (self.dk_btn, self.na_btn, self.later_btn):
            b.setEnabled(not opening)
        self._refresh_items()
        self._set_busy("")
        self._speak(q.text)

    def _refresh_items(self) -> None:
        eng = self.engine
        self.items.clear()
        for it in eng._items_in_order():
            st = eng.states[it.id]
            text = f"{STATUS_MARK[st.status]} {it.label}"
            if st.value:
                text += f"：{st.value[:24]}"
            self.items.addItem(text)

    # ---- 録音 ----
    def toggle_record(self) -> None:
        if self.recorder.recording:
            audio = self.recorder.stop()
            self._set_record_button(False)
            if len(audio) < 16000 * 0.5:
                self._set_busy("")
                return
            self._set_busy("音声を文字にしています…")
            run_task(lambda: self.win.services.asr().transcribe(audio).text,
                     lambda text: self._transcribed(audio, text), self._failed)
        else:
            self.win.speaker.stop()
            try:
                self.recorder.start()
            except Exception as e:  # noqa: BLE001
                QMessageBox.warning(self, "マイク", f"マイクを使えません。Windows のマイクの設定を確認してください。\n{e}")
                return
            self._set_record_button(True)
            self._timer.start()

    def _transcribed(self, audio: np.ndarray, text: str) -> None:
        self._set_busy("")
        if not text:
            self.busy_label.setText("声を聞き取れませんでした。もう一度話してください。")
            return
        # 続けて話した分は後ろに足す（言い直し・付け足しができる）
        current = self.answer.toPlainText().strip()
        self.answer.setPlainText(f"{current}{text}" if current else text)
        self._raw = f"{self._raw}{text}" if self._raw else text
        self._audio = audio if self._audio is None else np.concatenate([self._audio, audio])

    def _set_record_button(self, recording: bool) -> None:
        self.record_btn.setText("■ 話し終わったら押す" if recording else "● 押してから話す")
        self.record_btn.setProperty("recording", "true" if recording else "false")
        self.record_btn.style().unpolish(self.record_btn)
        self.record_btn.style().polish(self.record_btn)

    def _tick(self) -> None:
        if self.recorder.recording:
            self.level.setValue(min(100, int(self.recorder.level * 400)))
            self.busy_label.setText(f"録音中… {self.recorder.seconds:.0f}秒")
            if self.recorder.seconds > Recorder.MAX_SECONDS:
                self.toggle_record()
        else:
            self.level.setValue(0)
            if self._busy_text:
                self._busy_since += 1
                self.busy_label.setText(f"{self._busy_text}（{self._busy_since // 10}秒）")
            else:
                self._timer.stop()

    # ---- 回答 ----
    def submit(self) -> None:
        if self.recorder.recording:
            self.toggle_record()
            return
        text = self.answer.toPlainText().strip()
        if not text:
            QMessageBox.information(self, "回答", "回答を話すか入力してください。分からないときは「分からない」を押してください。")
            return
        raw = self._raw if self._raw is not None else text
        audio = self._audio
        eng, case_id, reporter = self.engine, self.case_id, self.reporter
        first = not eng.answers
        self._set_busy("回答を整理しています…")

        def work():
            ans = eng.submit_answer(text, raw_text=raw, speaker=reporter)
            if audio is not None and len(audio):
                write_wav(audio_dir(case_id) / f"{ans.answer_id}.wav", audio)
            return eng.next_question()

        def done(q):
            if first:
                self.win.store.save_case(case_id, title=text.replace("\n", " ")[:40])
            self._save()
            self.show_question(q)

        run_task(work, done, self._failed)

    def press(self, button: str) -> None:
        self.engine.mark_button(button)
        q = self.engine.next_question()
        self._save()
        self.show_question(q)

    def pause(self) -> None:
        if self.recorder.recording:
            self.recorder.stop()
            self._set_record_button(False)
        self._save()
        self.win.show_start()

    def finish_early(self) -> None:
        left = len(self.engine.unresolved())
        msg = "聞き取りを終えて、報告書の文章を作りますか？"
        if left:
            msg += f"\n\nまだ確認できていない項目が {left} 件あります。報告書には「未確認事項」として書きます。"
        if QMessageBox.question(self, "聞き取りの終了", msg) == QMessageBox.StandardButton.Yes:
            self._go_review()

    def _go_review(self) -> None:
        self.engine.current = None
        self._save()
        self.win.show_review(self.case_id)

    # ---- 共通 ----
    def _save(self) -> None:
        self.win.store.save_case(self.case_id, engine=self.engine.snapshot())

    def _speak(self, text: str) -> None:
        if self.win.store.setting("tts_enabled") == "1":
            self.win.speaker.say(text)

    def _set_busy(self, text: str) -> None:
        self._busy_text = text
        self._busy_since = 0
        busy = bool(text)
        for w in (self.submit_btn, self.record_btn, self.dk_btn, self.na_btn, self.later_btn,
                  self.finish_btn, self.pause_btn, self.answer):
            w.setEnabled(not busy)
        if busy:
            self.busy_label.setText(text)
            self._timer.start()
        else:
            self.busy_label.setText("")
            if self.engine and self.engine.current and self.engine.current.item_id is None:
                for b in (self.dk_btn, self.na_btn, self.later_btn):
                    b.setEnabled(False)

    def _failed(self, message: str) -> None:
        self._set_busy("")
        QMessageBox.warning(self, "エラー", f"処理に失敗しました。もう一度試してください。\n{message}")
