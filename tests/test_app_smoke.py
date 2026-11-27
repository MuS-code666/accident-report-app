"""画面の通し動作テスト（AI・音声認識・読み上げは偽物。画面は表示しない）。"""

import os

import openpyxl
import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

from PySide6.QtCore import QThreadPool  # noqa: E402
from PySide6.QtWidgets import QApplication, QMessageBox  # noqa: E402

from accident_report.export import notice_excel  # noqa: E402
from accident_report.storage import STATUS_EXPORTED, STATUS_INTERVIEW, Store  # noqa: E402
from accident_report.writing.notice import NoticeDraft  # noqa: E402

from accident_report.models.scene import DiagramSpec  # noqa: E402

from test_diagram_questions import spec_with_gaps  # noqa: E402
from test_engine import fact, result  # noqa: E402

pytestmark = pytest.mark.skipif(not notice_excel.TEMPLATE.exists(), reason="暫定テンプレートがない（Git対象外）")


class FakeClient:
    def __init__(self):
        self.model = "fake"

    def warm_up(self):
        pass

    def generate_json(self, system, prompt, schema, max_tokens=1024):
        if schema is DiagramSpec:
            return spec_with_gaps()
        return NoticeDraft.model_validate({
            "involved_persons": ["私"], "work_category": "運搬（車輌・フォーク）",
            "overview": [{"text": "フォークリフトで後退中、シャッターの柱に接触した。", "evidence": ["A001"]}],
            "cause": [], "countermeasure": []})


class FakeSpeaker:
    def say(self, text):
        pass

    def stop(self):
        pass


def wait(app):
    for _ in range(50):
        QThreadPool.globalInstance().waitForDone(100)
        app.processEvents()


@pytest.fixture
def window(tmp_path, monkeypatch):
    app = QApplication.instance() or QApplication([])
    from accident_report.app import main as app_main
    from accident_report.app.services import Services

    monkeypatch.setattr(notice_excel, "OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr("accident_report.app.review_page.OUTPUT_DIR", tmp_path / "output")
    monkeypatch.setattr("accident_report.app.interview_page.audio_dir", lambda cid: tmp_path / "audio" / str(cid))
    monkeypatch.setattr("accident_report.app.diagram_page.diagram_dir", lambda cid: tmp_path / "diagrams" / str(cid))
    monkeypatch.setattr(app_main, "Speaker", FakeSpeaker)
    monkeypatch.setattr(Services, "asr", lambda self: None)
    monkeypatch.setattr(Services, "check_ollama", lambda self: None)
    monkeypatch.setattr(Services, "client", lambda self, key: FakeClient())
    table = {"きのう14時ごろ、第一工場の搬出口でフォークリフトが柱に当たりました":
             result([fact("datetime", "きのう14時ごろ"), fact("location", "第一工場 搬出口")])}
    monkeypatch.setattr(Services, "extractor", lambda self: (lambda items, known, types, q, a: table.get(a, result())))
    monkeypatch.setattr(QMessageBox, "question", lambda *a, **k: QMessageBox.StandardButton.Yes)
    monkeypatch.setattr(QMessageBox, "exec", lambda self: 0)
    store = Store(tmp_path / "app.db")
    win = app_main.MainWindow(store)
    yield app, win, store
    win.close()


def test_interview_resume_review_and_export(window):
    app, win, store = window
    win.start_page.user.setCurrentText("山田")
    win.start_page.start_new()
    page = win.interview_page
    assert win.stack.currentWidget() is page and page.chip.text() == "最初の質問"

    page.answer.setPlainText("きのう14時ごろ、第一工場の搬出口でフォークリフトが柱に当たりました")
    page.submit()
    wait(app)
    assert page.engine.current.item_id == "persons_involved"  # 日時・場所は聞き取り済みなので飛ばす
    page.press("dont_know")
    page.pause()
    assert win.stack.currentWidget() is win.start_page

    case_id = store.list_cases()[0].id
    assert store.load_case(case_id).status == STATUS_INTERVIEW
    win.open_case(case_id)  # 再開
    assert page.engine.current.item_id == "reporter_role"
    page.finish_early()
    wait(app)

    review = win.review_page
    assert win.stack.currentWidget() is review
    assert review.dt.text().endswith("14時ごろ") and review.location.text() == "第一工場 搬出口"
    assert "事故当事者（不明・確認中）" in review.unresolved.toPlainText()
    review.export()
    row = store.load_case(case_id)
    assert row.status == STATUS_EXPORTED
    ws = openpyxl.load_workbook(row.output_path).worksheets[0]
    assert ws["F3"].value == "山田" and ws["E5"].value == "運搬（車輌・フォーク）"
    assert "【未確認事項】" in ws["B7"].value


def test_diagram_step_answers_questions_and_figure_goes_into_excel(window):
    app, win, store = window
    win.start_page.user.setCurrentText("山田")
    win.start_page.start_new()
    page = win.interview_page
    page.answer.setPlainText("きのう14時ごろ、第一工場の搬出口でフォークリフトが柱に当たりました")
    page.submit()
    wait(app)
    page.finish_early()
    wait(app)
    review = win.review_page
    assert "まだ作っていません" in review.fig_status.text()
    review.open_diagram()
    wait(app)
    dpage = win.diagram_page
    assert win.stack.currentWidget() is dpage and dpage.question is not None
    for _ in range(20):  # 追加の質問に最初の選択肢で答えていく
        if dpage.question is None:
            break
        dpage.choose(dpage.question.choices[0])
    assert dpage.question is None and dpage.image.pixmap() is not None
    dpage.accept()
    assert win.stack.currentWidget() is review and "報告書に貼ります" in review.fig_status.text()
    review.export()
    row = store.load_case(review.case_id)
    assert row.diagram["accepted"] and row.diagram["answers"]
    ws = openpyxl.load_workbook(row.output_path).worksheets[0]
    assert len(ws._images) == 1
