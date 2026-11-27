"""作図の流れ（追加質問・図の保存・Excelへの貼り付け）のテスト（LLMなし）。"""

import openpyxl
import pytest

from accident_report.diagram import service
from accident_report.diagram.questions import DONT_KNOW
from accident_report.export.notice_excel import TEMPLATE, NoticeForm, write_notice
from accident_report.models.scene import DiagramSpec

from test_diagram_questions import spec_with_gaps
from test_engine import make


def three_scene_spec():
    spec = spec_with_gaps().model_dump()
    spec["scenes"].append({"scene_id": "s3", "phase": "事故発生後", "anchor": "ex"})
    return DiagramSpec.model_validate(spec)


def test_questions_are_not_asked_twice_and_answers_are_recorded():
    state = service.DiagramState(spec=spec_with_gaps())
    first = service.pending_questions(state)
    assert first
    for q in first:
        service.answer(state, q, DONT_KNOW)
    assert service.pending_questions(state) == []  # 「分からない」でも同じ質問は繰り返さない
    assert len(state.answers) == len(first)
    restored = service.DiagramState.model_validate(state.model_dump(mode="json"))  # DB保存と同じ往復
    assert restored.asked() == state.asked()


def test_excel_scenes_pick_accident_and_previous():
    from accident_report.diagram.layout import layout
    lays = layout(three_scene_spec())
    assert service.excel_scenes(lays) == [0, 1]  # 事故直前・事故発生時


def test_diagram_text_skips_cause_and_keeps_questions():
    eng = make({})
    eng.next_question()
    eng.submit_answer("重機が門の柱に当たりました")
    eng.next_question()
    eng.submit_answer("佐藤さんです")
    text = service.diagram_text(eng)
    assert text.splitlines()[0] == "重機が門の柱に当たりました。"
    assert "（" in text.splitlines()[1] and "佐藤さんです。" in text


@pytest.mark.skipif(not TEMPLATE.exists(), reason="暫定テンプレートがない（Git対象外）")
def test_figures_are_pasted_into_notice(tmp_path):
    state = service.DiagramState(spec=three_scene_spec())
    main, extra, _ = service.render(state, tmp_path, "case1")
    assert main.exists() and extra is not None and extra.exists()
    out = write_notice(NoticeForm(overview=["重機が門の柱に接触。"]), tmp_path / "o.xlsx",
                       figure=main, extra_figure=extra)
    wb = openpyxl.load_workbook(out)
    ws = wb.worksheets[0]
    assert ws["B7"].value.startswith("・重機")
    assert "B7:I11" in {str(m) for m in ws.merged_cells.ranges}  # 文章が短いので図を大きくする
    assert len(ws._images) == 1
    assert "別紙（事故状況図）" in wb.sheetnames and len(wb["別紙（事故状況図）"]._images) == 1
