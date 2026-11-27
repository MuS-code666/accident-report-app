"""設定画面: 利用者・用語（社員・重機・設備など）・その他の設定。"""

from __future__ import annotations

from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
    QWidget,
)

from accident_report.storage import TERM_CATEGORIES, Store

ROLES = ["作業員", "管理監督者"]


class _ListTab(QWidget):
    """名前と区分の一覧に、追加・削除ボタンを付けた画面。"""

    def __init__(self, help_text: str, kinds: list[str], load, add, remove):
        super().__init__()
        self.load, self.add, self.remove = load, add, remove
        lay = QVBoxLayout(self)
        note = QLabel(help_text)
        note.setWordWrap(True)
        note.setObjectName("muted")
        lay.addWidget(note)
        self.list = QListWidget()
        lay.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.edit = QLineEdit()
        self.edit.setPlaceholderText("追加する名前")
        self.edit.returnPressed.connect(self._add)
        self.kind = QComboBox()
        self.kind.addItems(kinds)
        add_btn = QPushButton("追加")
        add_btn.clicked.connect(self._add)
        del_btn = QPushButton("選んだものを削除")
        del_btn.clicked.connect(self._remove)
        row.addWidget(self.edit, 1)
        row.addWidget(self.kind)
        row.addWidget(add_btn)
        row.addWidget(del_btn)
        lay.addLayout(row)
        self.refresh()

    def refresh(self) -> None:
        self.list.clear()
        for name, kind in self.load():
            self.list.addItem(f"{name}　［{kind}］")

    def _add(self) -> None:
        if self.edit.text().strip():
            self.add(self.edit.text(), self.kind.currentText())
            self.edit.clear()
            self.refresh()

    def _remove(self) -> None:
        item = self.list.currentItem()
        if item:
            self.remove(item.text().split("　［")[0])
            self.refresh()


class SettingsDialog(QDialog):
    def __init__(self, store: Store, parent=None):
        super().__init__(parent)
        self.store = store
        self.setWindowTitle("設定")
        self.resize(640, 520)
        lay = QVBoxLayout(self)
        tabs = QTabWidget()
        lay.addWidget(tabs)

        tabs.addTab(_ListTab("報告を作る人の名前です。起動時にここから選びます（権限の区別はありません）。",
                             ROLES, store.users, store.add_user, store.remove_user), "利用者")
        tabs.addTab(_ListTab("社員の名前・重機や設備の呼び名・場所の名前などを登録すると、"
                             "音声認識で正しく書き取られやすくなります。",
                             TERM_CATEGORIES, store.terms, store.add_term, store.remove_term), "用語")

        other = QWidget()
        form = QFormLayout(other)
        self.tts = QCheckBox("質問を音声で読み上げる")
        self.tts.setChecked(store.setting("tts_enabled") == "1")
        self.delete_audio = QCheckBox("報告書をExcelに出力したら、録音を削除する")
        self.delete_audio.setChecked(store.setting("delete_audio_after_export") == "1")
        self.asr = QComboBox()
        self.asr.addItems(["large-v3-turbo", "medium", "small"])
        self.asr.setCurrentText(store.setting("asr_model"))
        self.writer = QComboBox()
        self.writer.addItems(["qwen3:8b", "qwen3:4b"])
        self.writer.setCurrentText(store.setting("writer_model"))
        form.addRow(self.tts)
        form.addRow(self.delete_audio)
        form.addRow("音声認識のモデル", self.asr)
        form.addRow("文章を作るAIモデル", self.writer)
        hint = QLabel("処理が遅い場合は、文章のモデルを qwen3:4b、音声認識を medium にすると速くなります。")
        hint.setWordWrap(True)
        hint.setObjectName("muted")
        form.addRow(hint)
        tabs.addTab(other, "その他")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        lay.addWidget(buttons)

    def done(self, r: int) -> None:  # 閉じるときに保存
        self.store.set_setting("tts_enabled", "1" if self.tts.isChecked() else "0")
        self.store.set_setting("delete_audio_after_export", "1" if self.delete_audio.isChecked() else "0")
        self.store.set_setting("asr_model", self.asr.currentText())
        self.store.set_setting("writer_model", self.writer.currentText())
        super().done(r)
