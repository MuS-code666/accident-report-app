"""開始画面: 名前を選ぶ・新しい報告を始める・途中の報告を再開する。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from accident_report.app.services import run_task

if TYPE_CHECKING:
    from accident_report.app.main import MainWindow


class StartPage(QWidget):
    def __init__(self, win: "MainWindow"):
        super().__init__()
        self.win = win
        lay = QVBoxLayout(self)
        lay.setContentsMargins(40, 30, 40, 30)

        title = QLabel("事故報告書 作成支援")
        title.setObjectName("title")
        lay.addWidget(title)
        self.status = QLabel("")
        self.status.setWordWrap(True)
        lay.addWidget(self.status)

        row = QHBoxLayout()
        row.addWidget(QLabel("あなたの名前"))
        self.user = QComboBox()
        self.user.setEditable(True)
        self.user.setMinimumWidth(260)
        self.user.lineEdit().setPlaceholderText("選ぶか入力してください")
        row.addWidget(self.user)
        row.addStretch()
        lay.addLayout(row)

        self.new_btn = QPushButton("新しい事故報告を始める")
        self.new_btn.setObjectName("primary")
        self.new_btn.clicked.connect(self.start_new)
        lay.addWidget(self.new_btn)

        lay.addSpacing(16)
        lay.addWidget(QLabel("これまでの報告（途中のものは続きから再開できます）"))
        self.cases = QListWidget()
        self.cases.itemDoubleClicked.connect(lambda _: self.resume())
        lay.addWidget(self.cases, 1)

        row2 = QHBoxLayout()
        self.resume_btn = QPushButton("選んだ報告を開く")
        self.resume_btn.clicked.connect(self.resume)
        self.delete_btn = QPushButton("選んだ報告を削除")
        self.delete_btn.clicked.connect(self.delete)
        settings = QPushButton("設定（利用者・用語・その他）")
        settings.clicked.connect(self.win.open_settings)
        row2.addWidget(self.resume_btn)
        row2.addWidget(self.delete_btn)
        row2.addStretch()
        row2.addWidget(settings)
        lay.addLayout(row2)

    def refresh(self) -> None:
        current = self.user.currentText()
        self.user.clear()
        self.user.addItems([name for name, _ in self.win.store.users()])
        if current:
            self.user.setCurrentText(current)
        self.cases.clear()
        for c in self.win.store.list_cases():
            text = f"{c.updated_at:%m/%d %H:%M}　{c.status}　{c.reporter}　{c.title or '（内容未入力）'}"
            item = QListWidgetItem(text)
            item.setData(Qt.ItemDataRole.UserRole, c.id)
            self.cases.addItem(item)
        self.status.setText("AIの準備を確認しています…")
        self.status.setObjectName("muted")
        run_task(self.win.services.check_ollama, self._show_ai_status, lambda e: self._show_ai_status(e))

    def _show_ai_status(self, problem) -> None:
        if problem:
            self.status.setText(f"⚠ {problem}　（AIなしでも定型の質問で聞き取りはできます）")
            self.status.setObjectName("warn")
        else:
            self.status.setText("AIの準備ができています。")
            self.status.setObjectName("muted")
        self.status.style().unpolish(self.status)
        self.status.style().polish(self.status)

    def _reporter(self) -> str | None:
        name = self.user.currentText().strip()
        if not name:
            QMessageBox.information(self, "名前", "あなたの名前を選ぶか入力してください。")
            return None
        if name not in [n for n, _ in self.win.store.users()]:
            self.win.store.add_user(name)
        return name

    def start_new(self) -> None:
        name = self._reporter()
        if name:
            self.win.start_new_case(name)

    def _selected(self) -> int | None:
        item = self.cases.currentItem()
        if item is None:
            QMessageBox.information(self, "報告", "一覧から報告を選んでください。")
            return None
        return int(item.data(Qt.ItemDataRole.UserRole))

    def resume(self) -> None:
        case_id = self._selected()
        if case_id is not None:
            self.win.open_case(case_id)

    def delete(self) -> None:
        case_id = self._selected()
        if case_id is None:
            return
        ok = QMessageBox.question(self, "削除", "選んだ報告と録音を削除します。元に戻せません。よろしいですか？")
        if ok == QMessageBox.StandardButton.Yes:
            self.win.store.delete_case(case_id)
            self.refresh()
