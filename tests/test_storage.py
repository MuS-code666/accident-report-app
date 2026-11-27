"""保存・再開のテスト（一時フォルダーのDBを使う）。"""

from accident_report.interview.engine import InterviewEngine
from accident_report.interview.questions import load_question_set
from accident_report.models.interview import ItemStatus
from accident_report.storage import STATUS_REVIEW, Store, audio_dir

from test_engine import ScriptedExtractor, fact, result


def test_interview_can_be_resumed_from_database(tmp_path):
    store = Store(tmp_path / "app.db")
    case_id = store.create_case("山田")
    ext = ScriptedExtractor({"昨日、入口で柱に当たった": result([fact("location", "入口")])})
    eng = InterviewEngine(load_question_set(), ext)
    eng.next_question()
    eng.submit_answer("昨日、入口で柱に当たった")
    q = eng.next_question()
    store.save_case(case_id, engine=eng.snapshot(), title="入口で柱に接触")
    store.close()

    store2 = Store(tmp_path / "app.db")
    row = store2.load_case(case_id)
    eng2 = InterviewEngine.restore(load_question_set(), ext, row.engine)
    assert row.title == "入口で柱に接触" and row.reporter == "山田"
    assert eng2.current.item_id == q.item_id  # 中断した質問から再開
    assert eng2.states["location"].status == ItemStatus.ANSWERED
    assert eng2.states[q.item_id].times_asked == 1
    eng2.submit_answer("10月23日の14時ごろです")
    assert eng2.answers[-1].answer_id == "A002"  # 回答IDは続き番号


def test_users_terms_settings_and_case_list(tmp_path):
    store = Store(tmp_path / "app.db")
    store.add_user("山田", "管理監督者")
    store.add_term("フレコン", "設備")
    assert store.users() == [("山田", "管理監督者")]
    assert ("フレコン", "設備") in store.terms()
    assert store.setting("delete_audio_after_export") == "1"
    store.set_setting("delete_audio_after_export", "0")
    assert store.setting("delete_audio_after_export") == "0"
    a = store.create_case("山田")
    store.save_case(a, status=STATUS_REVIEW)
    assert [c.id for c in store.list_cases()] == [a]


def test_delete_case_removes_audio(tmp_path):
    store = Store(tmp_path / "app.db")
    case_id = store.create_case("山田")
    d = audio_dir(case_id, tmp_path)
    d.mkdir(parents=True)
    (d / "A001.wav").write_bytes(b"x")
    store.delete_case(case_id)
    assert not d.exists()
