"""アプリの起動と画面の切り替え。

起動: .venv\\Scripts\\pythonw -m accident_report.app （または start_app.bat）
"""

from __future__ import annotations

import os
import sys

os.environ.setdefault("HF_HUB_DISABLE_XET", "1")  # ネイティブDLL（hf_xet）を使わない
os.environ.setdefault("HF_HUB_OFFLINE", "1")  # 起動後はネットワークを使わない

from PySide6.QtWidgets import QApplication, QMainWindow, QMessageBox, QStackedWidget  # noqa: E402

from accident_report.app.diagram_page import DiagramPage  # noqa: E402
from accident_report.app.interview_page import InterviewPage  # noqa: E402
from accident_report.app.review_page import ReviewPage  # noqa: E402
from accident_report.app.services import Services, Speaker, run_task  # noqa: E402
from accident_report.app.settings_dialog import SettingsDialog  # noqa: E402
from accident_report.app.start_page import StartPage  # noqa: E402
from accident_report.app.style import setup_app  # noqa: E402
from accident_report.interview.engine import InterviewEngine  # noqa: E402
from accident_report.storage import STATUS_INTERVIEW, Store  # noqa: E402


class MainWindow(QMainWindow):
    def __init__(self, store: Store):
        super().__init__()
        self.store = store
        self.services = Services(store)
        self.speaker = Speaker()
        self.setWindowTitle("事故報告書 作成支援")
        self.resize(1200, 820)

        self.stack = QStackedWidget()
        self.setCentralWidget(self.stack)
        self.start_page = StartPage(self)
        self.interview_page = InterviewPage(self)
        self.review_page = ReviewPage(self)
        self.diagram_page = DiagramPage(self)
        for p in (self.start_page, self.interview_page, self.review_page, self.diagram_page):
            self.stack.addWidget(p)
        self.show_start()
        # 音声認識モデルは読み込みに時間がかかるので、起動直後に裏で読み込んでおく
        run_task(self.services.asr, None, lambda e: None)

    def _engine(self, data: dict | None) -> InterviewEngine:
        if data:
            return InterviewEngine.restore(self.services.qset, self.services.extractor(), data)
        return InterviewEngine(self.services.qset, self.services.extractor())

    # ---- 画面の切り替え ----
    def show_start(self) -> None:
        self.speaker.stop()
        self.start_page.refresh()
        self.stack.setCurrentWidget(self.start_page)

    def start_new_case(self, reporter: str) -> None:
        case_id = self.store.create_case(reporter)
        self.stack.setCurrentWidget(self.interview_page)
        self.interview_page.open(case_id, self._engine(None), reporter)

    def open_case(self, case_id: int) -> None:
        row = self.store.load_case(case_id)
        if row.status == STATUS_INTERVIEW:
            self.resume_interview(case_id)
        else:
            self.show_review(case_id)

    def resume_interview(self, case_id: int) -> None:
        row = self.store.load_case(case_id)
        self.store.save_case(case_id, status=STATUS_INTERVIEW)
        self.stack.setCurrentWidget(self.interview_page)
        self.interview_page.open(case_id, self._engine(row.engine), row.reporter)

    def show_review(self, case_id: int) -> None:
        self.speaker.stop()
        row = self.store.load_case(case_id)
        self.stack.setCurrentWidget(self.review_page)
        self.review_page.open(case_id, self._engine(row.engine), row.reporter, row.draft, row.diagram)

    def show_diagram(self, case_id: int) -> None:
        row = self.store.load_case(case_id)
        self.stack.setCurrentWidget(self.diagram_page)
        self.diagram_page.open(case_id, self._engine(row.engine), row.diagram)

    def open_settings(self) -> None:
        SettingsDialog(self.store, self).exec()
        self.start_page.refresh()

    def closeEvent(self, event) -> None:  # noqa: N802
        if self.interview_page.recorder.recording:
            self.interview_page.recorder.stop()
        self.store.close()
        super().closeEvent(event)


def main() -> int:
    app = QApplication(sys.argv)
    setup_app(app)
    try:
        store = Store()
    except Exception as e:  # noqa: BLE001
        QMessageBox.critical(None, "起動できません", f"データを開けませんでした。\n{e}")
        return 1
    win = MainWindow(store)
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
