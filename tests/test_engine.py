"""インタビュー進行管理のテスト（LLMは偽物に置き換える）。"""

from types import SimpleNamespace as NS

from accident_report.interview.engine import InterviewEngine
from accident_report.interview.questions import load_question_set
from accident_report.models.interview import ItemStatus


def fact(item_id, value, complete=True, contradicts=False, source="本人の体験"):
    return NS(item_id=item_id, value=value, source_type=source,
              complete=complete, contradicts_known=contradicts)


def result(facts=(), types=(), dont_know=False, not_applicable=False):
    return NS(facts=list(facts), accident_types=list(types),
              dont_know=dont_know, not_applicable=not_applicable)


class ScriptedExtractor:
    """回答文 → 抽出結果 の対応表で動く偽の抽出器。"""

    def __init__(self, table):
        self.table = table
        self.calls = []

    def __call__(self, items, known, types, question, answer):
        self.calls.append((question, answer))
        return self.table.get(answer, result())


def make(table):
    return InterviewEngine(load_question_set(), ScriptedExtractor(table))


def test_opening_fills_multiple_items_and_skips_them():
    eng = make({
        "昨日の14時ごろ第二工場の入口で重機が柱に当たった": result(
            [fact("datetime", "昨日14時ごろ"), fact("location", "第二工場 入口"),
             fact("accident_event", "重機が柱に接触")]),
    })
    assert eng.next_question().kind == "opening"
    eng.submit_answer("昨日の14時ごろ第二工場の入口で重機が柱に当たった")
    asked = []
    for _ in range(3):
        q = eng.next_question()
        asked.append(q.item_id)
        eng.mark_button("dont_know")
    assert "datetime" not in asked and "location" not in asked
    assert asked[0] == "persons_involved"  # 必須の未確認項目を優先度順に聞く


def test_insufficient_item_is_deepened_once():
    eng = make({
        "概要": result([fact("datetime", "10月23日", complete=False)]),
        "14時ごろです": result([fact("datetime", "14時ごろ")]),
    })
    eng.next_question()
    eng.submit_answer("概要")
    assert eng.states["datetime"].status == ItemStatus.INSUFFICIENT
    # 必須の未確認項目をすべて片付けると、深掘りの質問が来る
    while True:
        q = eng.next_question()
        if q.kind == "deepen":
            break
        eng.mark_button("dont_know")
    assert q.item_id == "datetime"
    eng.submit_answer("14時ごろです")
    st = eng.states["datetime"]
    assert st.status == ItemStatus.ANSWERED
    assert "10月23日" in st.value and "14時ごろ" in st.value


def test_conflict_is_confirmed_once_then_resolved():
    eng = make({
        "概要": result([fact("location", "第二工場")]),
        "第一工場でした": result([fact("location", "第一工場", contradicts=True)]),
        "第一工場が正しいです": result([fact("location", "第一工場")]),
    })
    eng.next_question()
    eng.submit_answer("概要")
    eng.next_question()  # datetime
    eng.submit_answer("第一工場でした")
    assert eng.states["location"].status == ItemStatus.CONFLICT
    q = eng.next_question()
    assert q.kind == "conflict" and q.item_id == "location"
    eng.submit_answer("第一工場が正しいです")
    assert eng.states["location"].status == ItemStatus.ANSWERED
    assert eng.states["location"].value == "第一工場"


def test_unresolved_conflict_is_not_asked_forever():
    eng = make({
        "概要": result([fact("location", "第二工場")]),
        "第一工場": result([fact("location", "第一工場", contradicts=True)]),
    })
    eng.next_question()
    eng.submit_answer("概要")
    eng.next_question()
    eng.submit_answer("第一工場")
    assert eng.next_question().kind == "conflict"
    eng.submit_answer("よく分からない")
    for _ in range(60):
        q = eng.next_question()
        if q is None:
            break
        assert q.kind != "conflict"
        eng.mark_button("dont_know")
    assert ("発生場所", ItemStatus.CONFLICT) in eng.unresolved()


def test_accident_type_adds_type_questions_and_reextracts():
    ext_table = {
        "重機が柱に当たった": result([fact("accident_event", "重機が柱に接触")],
                                types=["machine_vs_facility"]),
    }
    eng = make(ext_table)
    eng.next_question()
    eng.submit_answer("重機が柱に当たった")
    assert "machine_type" in eng.states
    # 類型が決まった直後に、これまでの回答から類型別項目を拾い直す
    assert any(q == "これまでの聞き取り" for q, _ in eng.extractor.calls)


def test_extractor_failure_does_not_stop_interview():
    def broken(*args):
        raise RuntimeError("LLM down")

    eng = InterviewEngine(load_question_set(), broken)
    eng.next_question()
    eng.submit_answer("何か")
    q = eng.next_question()
    assert q is not None and q.item_id == "datetime"
    assert any("抽出失敗" in line for line in eng.log)


def test_interview_ends_and_lists_unresolved():
    eng = make({})
    eng.next_question()
    eng.submit_answer("概要")
    count = 0
    while (q := eng.next_question()) is not None:
        eng.mark_button("dont_know")
        count += 1
        assert count < 100
    labels = [label for label, _ in eng.unresolved()]
    assert "事故発生日時" in labels


def test_later_button_asks_again_at_the_end():
    eng = make({})
    eng.next_question()
    eng.submit_answer("概要")
    q = eng.next_question()
    assert q.item_id == "datetime"
    eng.mark_button("later")
    seen = []
    while (q := eng.next_question()) is not None:
        seen.append((q.kind, q.item_id))
        eng.mark_button("dont_know")
    assert seen[-1] == ("deferred", "datetime")


def test_raw_answer_is_used_when_llm_misses_asked_item():
    eng = make({"運転してたのは自分、田中です": result([fact("operator", "田中")])})
    eng.next_question()
    eng.submit_answer("概要")
    q = eng.next_question()
    while q.item_id != "persons_involved":
        eng.mark_button("dont_know")
        q = eng.next_question()
    eng.submit_answer("運転してたのは自分、田中です")
    st = eng.states["persons_involved"]
    assert st.status == ItemStatus.ANSWERED and "田中" in st.value
    assert eng.next_question().item_id != "persons_involved"  # 同じ質問を繰り返さない


def test_answered_value_is_not_overwritten_by_other_answers():
    eng = make({
        "概要": result([fact("location", "第一工場の搬出口")]),
        "搬出口まで運ぶ作業": result([fact("work_content", "搬出口まで運ぶ作業"),
                                   fact("location", "搬出口まで")]),
    })
    eng.next_question()
    eng.submit_answer("概要")
    eng.next_question()
    eng.submit_answer("搬出口まで運ぶ作業")
    assert eng.states["location"].value == "第一工場の搬出口"
