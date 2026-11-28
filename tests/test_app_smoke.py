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
from accident_report.writing.detail import DetailSuggestion  # noqa: E402

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
        if schema is DetailSuggestion:
            return DetailSuggestion.model_validate({
                "causes": [{"category": "人", "text": "後方確認が不足していた。", "evidence": ["A001"]}],
                "measures": [{"text": "後退時の指差し確認を徹底する。"}]})
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


def test_diagram_editor_records_moves_rotation_and_hidden(window):
    from accident_report.app.diagram_editor import DiagramEditor
    from accident_report.diagram import service
    state = service.DiagramState(spec=spec_with_gaps())
    ed = DiagramEditor(state)
    items = ed.items[0]
    x, y = items["ex"].center_m()
    items["ex"].place(x + 2, y, 30)  # 重機を右へ2m動かして30度回す
    items["pillar"].setSelected(True)
    ed._toggle_hidden()
    ed._accept()
    sid = state.spec.scenes[0].scene_id
    mp = state.manual[sid]["ex"]
    assert abs(mp.x - (x + 2)) < 0.01 and mp.heading == 30
    assert state.hidden[sid] == ["pillar"]
    assert "wall" not in state.manual[sid]  # 動かしていない物は記録しない


def _finished_notice(app, win):
    win.start_page.user.setCurrentText("山田")
    win.start_page.start_new()
    page = win.interview_page
    page.answer.setPlainText("きのう14時ごろ、第一工場の搬出口でフォークリフトが柱に当たりました")
    page.submit()
    wait(app)
    page.finish_early()
    wait(app)
    win.review_page.export()
    return win.review_page.case_id


def test_detail_report_from_notice_with_ai_suggestions_and_export(window):
    app, win, store = window
    case_id = _finished_notice(app, win)
    win.start_page.user.setCurrentText("班長 木村")
    win.show_detail(case_id)
    d = win.detail_page
    assert win.stack.currentWidget() is d
    assert d.location.text() == "第一工場 搬出口" and d.dt.text().endswith("14時ごろ")
    d.ask_ai()
    wait(app)
    assert d.causes.rowCount() == 1 and d.measures.rowCount() == 1
    assert d.causes.item(0, 1).text() == "後方確認が不足。"
    assert d.causes.item(0, 2).text().startswith("AI案")
    d.measures.setItem(0, 1, type(d.causes.item(0, 1))("木村"))
    d.measures.setItem(0, 2, type(d.causes.item(0, 1))("2026年12月15日"))
    d.risk[("before", 0)].setText("3")
    d.risk[("before", 3)].setText("Ⅱ")
    d.export()
    row = store.load_case(case_id)
    assert row.detail["supervisor"] == "班長 木村" and row.detail["causes"][0]["evidence"] == ["A001"]
    ws = openpyxl.load_workbook(row.detail_output_path).worksheets[0]
    assert ws["B23"].value == "・【人】後方確認が不足。"
    assert ws["B27"].value.startswith("・後退時の指差し確認を徹底。（担当：木村、期限：2026年12月15日）")
    assert ws["C33"].value == 3 and ws["F33"].value == "Ⅱ"


def test_detail_only_case_and_measures_progress(window):
    from datetime import date

    from accident_report.app.measures_dialog import MeasuresDialog, is_overdue
    from accident_report.models.detail import DetailReport, Measure

    app, win, store = window
    win.start_page.user.setCurrentText("班長 木村")
    win.start_page.start_detail_only()
    d = win.detail_page
    assert win.stack.currentWidget() is d and not d.ai_btn.isEnabled()  # 聞き取りがないのでAI候補は使わない
    case_id = d.case_id
    rep_ = DetailReport(measures=[Measure(text="通路の仮置き禁止。", due="2026年11月1日"),
                                  Measure(text="手順書の改訂。", due="2026年11月1日", status="完了")])
    store.save_case(case_id, detail=rep_.model_dump(mode="json"))
    assert is_overdue("2026年11月1日", "未着手", date(2026, 11, 28))
    assert not is_overdue("2026年11月1日", "完了", date(2026, 11, 28))
    dlg = MeasuresDialog(store)
    assert dlg.table.rowCount() == 1  # 完了は隠す
    dlg.table.cellWidget(0, 4).setCurrentText("完了")
    dlg.accept()
    assert store.load_case(case_id).detail["measures"][0]["status"] == "完了"
