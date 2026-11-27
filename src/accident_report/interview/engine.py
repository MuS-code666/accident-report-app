"""インタビュー進行管理（Python側）。

LLMは回答の解析だけを担当し、次の質問・項目の状態・終了判定はここで決める。
LLMが失敗しても、定型の質問で聞き取りを続けられる。
"""

from __future__ import annotations

import itertools
import re
from dataclasses import dataclass, field
from typing import Callable, Protocol

from accident_report.interview.questions import QuestionItem, QuestionSet
from accident_report.models.interview import (
    Answer,
    ItemState,
    ItemStatus,
    SourceType,
    ValueRecord,
)

MAX_ASK = 2  # 同じ項目を聞く上限（最初の質問＋深掘り1回）
MAX_TYPES = 1  # 自動で有効にする事故類型の上限（複数候補は画面で選んでもらう想定）

DONT_KNOW = re.compile(r"(分から|わから|覚えて|知ら)(ない|ん)")

OPEN_STATUSES = {ItemStatus.UNCONFIRMED, ItemStatus.INSUFFICIENT}


class ExtractionLike(Protocol):
    facts: list
    accident_types: list
    dont_know: bool
    not_applicable: bool


Extractor = Callable[
    [list[QuestionItem], dict[str, str], dict[str, str], str, str], ExtractionLike
]


@dataclass
class Question:
    kind: str  # opening / item / deepen / conflict / deferred
    item_id: str | None
    text: str


@dataclass
class InterviewEngine:
    qset: QuestionSet
    extractor: Extractor
    states: dict[str, ItemState] = field(default_factory=dict)
    active_types: list[str] = field(default_factory=list)
    answers: list[Answer] = field(default_factory=list)
    current: Question | None = None
    log: list[str] = field(default_factory=list)
    _ids: itertools.count = field(default_factory=lambda: itertools.count(1))
    _opening_done: bool = False

    def __post_init__(self) -> None:
        for it in self.qset.common_items:
            self.states.setdefault(it.id, ItemState(item_id=it.id))

    # ---- 質問の選択 ----------------------------------------------------

    def next_question(self) -> Question | None:
        """次の質問を優先順位に従って選ぶ。聞くことがなければ None。"""
        if not self._opening_done:
            self.current = Question("opening", None, self.qset.opening_question)
            return self.current

        q = (
            self._pick_conflict()
            or self._pick(lambda it, st: it.required and it.group == "fact"
                          and st.status == ItemStatus.UNCONFIRMED and not st.deferred)
            or self._pick_deepen()
            or self._pick(lambda it, st: it.group in ("fact", "type")
                          and st.status == ItemStatus.UNCONFIRMED and not st.deferred)
            or self._pick(lambda it, st: it.group == "cause"
                          and st.status == ItemStatus.UNCONFIRMED and not st.deferred)
            or self._pick_deferred()
        )
        self.current = q
        if q and q.item_id:
            self.states[q.item_id].times_asked += 1
        return q

    def _items_in_order(self) -> list[QuestionItem]:
        items = [self.qset.item(i) for i in self.states]
        return sorted(items, key=lambda it: it.priority)

    def _pick(self, cond) -> Question | None:
        for it in self._items_in_order():
            st = self.states[it.id]
            if st.times_asked < MAX_ASK and cond(it, st):
                text = it.question if st.times_asked == 0 else f"もう一度伺います。{it.question}"
                return Question("item", it.id, text)
        return None

    def _pick_conflict(self) -> Question | None:
        for it in self._items_in_order():
            st = self.states[it.id]
            if st.status == ItemStatus.CONFLICT and not st.conflict_asked:
                st.conflict_asked = True  # 確認は1回だけ。解消しなければ「矛盾あり」のまま残す
                text = (f"{it.label}について、前に「{st.value}」、今は「{st.conflict_with}」"
                        "と伺いました。どちらが正しいですか？")
                return Question("conflict", it.id, text)
        return None

    def _pick_deepen(self) -> Question | None:
        for it in self._items_in_order():
            st = self.states[it.id]
            if st.status == ItemStatus.INSUFFICIENT and st.times_asked < MAX_ASK and not st.deferred:
                text = f"{it.label}について、もう少し詳しく教えてください。（今の内容：{st.value}）"
                return Question("deepen", it.id, text)
        return None

    def _pick_deferred(self) -> Question | None:
        for it in self._items_in_order():
            st = self.states[it.id]
            if st.deferred and st.status in OPEN_STATUSES:
                st.deferred = False  # 最後にもう一度だけ聞く
                st.times_asked = MAX_ASK - 1
                return Question("deferred", it.id, it.question)
        return None

    # ---- 回答の反映 ----------------------------------------------------

    def submit_answer(self, text: str, raw_text: str | None = None, speaker: str = "報告者") -> Answer:
        q = self.current
        if q is None:
            raise RuntimeError("質問がありません")
        answer = Answer(
            answer_id=f"A{next(self._ids):03d}",
            question_item_id=q.item_id,
            question_text=q.text,
            speaker=speaker,
            raw_text=raw_text if raw_text is not None else text,
            text=text,
        )
        self.answers.append(answer)
        if q.kind == "opening":
            self._opening_done = True

        items = self._llm_items()
        try:
            result = self.extractor(items, self.known_values(), self._type_labels(), q.text, text)
        except Exception as e:  # LLMが失敗しても聞き取りは続ける
            self.log.append(f"{answer.answer_id}: 抽出失敗 {e}")
            result = None

        if result is not None:
            # 事故類型は、事故の様子を話す回答（冒頭の説明・事故発生時の状況）からだけ決める
            new_types = []
            if q.kind == "opening" or q.item_id == "accident_event":
                new_types = self._activate_types(result.accident_types)
            self._apply(answer, q, result)
            if new_types:
                self._reextract_for_types(new_types)
        self._settle_asked_item(q, result, answer)
        return answer

    def mark_button(self, button: str) -> None:
        """画面のボタン（分からない / 該当なし / 後で回答する）。"""
        q = self.current
        if q is None or q.item_id is None:
            return
        st = self.states[q.item_id]
        if button == "dont_know":
            st.status = ItemStatus.UNKNOWN
        elif button == "not_applicable":
            st.status = ItemStatus.NOT_APPLICABLE
        elif button == "later":
            st.deferred = True
            st.times_asked = max(0, st.times_asked - 1)
        self.log.append(f"{q.item_id}: ボタン {button}")

    def _apply(self, answer: Answer, q: Question, result: ExtractionLike) -> None:
        for fact in result.facts:
            if fact.item_id not in self.states:
                continue  # 有効でない項目は無視（Python側で検証）
            st = self.states[fact.item_id]
            value = fact.value.strip()
            if not value:
                continue
            record = ValueRecord(value=value, source_type=SourceType(fact.source_type),
                                 answer_id=answer.answer_id)
            st.history.append(record)
            if answer.answer_id not in st.evidence:
                st.evidence.append(answer.answer_id)

            resolving = q.kind == "conflict" and q.item_id == fact.item_id
            has_value = st.value is not None and st.status in (ItemStatus.ANSWERED, ItemStatus.INSUFFICIENT)
            if resolving:
                st.value, st.conflict_with = value, None
                st.status = ItemStatus.ANSWERED
            elif has_value and fact.contradicts_known and value != st.value:
                st.conflict_with = value
                st.status = ItemStatus.CONFLICT
            elif st.status == ItemStatus.ANSWERED and q.item_id != fact.item_id:
                continue  # 確定済みの値は、聞いていない回答の言い換えで上書きしない
            else:
                if st.status == ItemStatus.INSUFFICIENT and st.value and st.value not in value:
                    value = f"{st.value}、{value}"
                st.value = value
                st.source_type = record.source_type
                st.status = ItemStatus.ANSWERED if fact.complete else ItemStatus.INSUFFICIENT

    def _settle_asked_item(self, q: Question, result: ExtractionLike | None, answer: Answer) -> None:
        if q.item_id is None:
            return
        st = self.states[q.item_id]
        if q.kind == "conflict":
            return  # 解消しなければ「矛盾あり」のまま未確認事項に残す
        if DONT_KNOW.search(answer.text) and len(answer.text) <= 20:
            st.status = ItemStatus.UNKNOWN  # 「分からない」はPython側でも判定する
        elif result is not None and result.dont_know:
            st.status = ItemStatus.UNKNOWN
        elif result is not None and result.not_applicable:
            st.status = ItemStatus.NOT_APPLICABLE
        elif st.status == ItemStatus.UNCONFIRMED and answer.text.strip():
            # 聞いた項目にAIが値を入れなかった: 回答文をそのまま値にする（同じ質問の繰り返しを防ぐ）
            st.value = answer.text.strip()
            st.status = ItemStatus.ANSWERED
            if answer.answer_id not in st.evidence:
                st.evidence.append(answer.answer_id)
            self.log.append(f"{q.item_id}: 回答文をそのまま値にした")

    def _activate_types(self, candidates: list[str]) -> list[str]:
        new = []
        for t in candidates:
            if t == "other" or t in self.active_types or len(self.active_types) >= MAX_TYPES:
                if t not in self.active_types and t != "other":
                    self.log.append(f"事故類型の別候補（未採用）: {t}")
                continue
            self.active_types.append(t)
            new.append(t)
            for item_id in self.qset.accident_types[t].items:
                self.states.setdefault(item_id, ItemState(item_id=item_id))
        if new:
            self.log.append(f"事故類型を追加: {new}")
        return new

    def _reextract_for_types(self, new_types: list[str]) -> None:
        """類型が決まったら、それまでの回答から類型別項目の情報を拾い直す（重複質問を防ぐ）。"""
        ids = {i for t in new_types for i in self.qset.accident_types[t].items}
        items = [self.qset.item(i) for i in ids]
        story = "\n".join(f"Q: {a.question_text}\nA: {a.text}" for a in self.answers)
        try:
            result = self.extractor(items, self.known_values(), {}, "これまでの聞き取り", story)
        except Exception as e:
            self.log.append(f"類型別項目の抽出失敗 {e}")
            return
        last = self.answers[-1]
        self._apply(last, Question("item", None, "これまでの聞き取り"), result)

    # ---- 参照 ------------------------------------------------------------

    def _llm_items(self) -> list[QuestionItem]:
        return [self.qset.item(i) for i in self.states]

    def _type_labels(self) -> dict[str, str]:
        return {t.id: f"{t.label}（{t.description}）" for t in self.qset.accident_types.values()}

    def known_values(self) -> dict[str, str]:
        return {
            self.qset.item(i).label: st.value
            for i, st in self.states.items()
            if st.value and st.status in (ItemStatus.ANSWERED, ItemStatus.INSUFFICIENT)
        }

    def unresolved(self) -> list[tuple[str, ItemStatus]]:
        """未確認事項（報告書に明記するもの）。"""
        out = []
        for it in self._items_in_order():
            st = self.states[it.id]
            if st.status in (ItemStatus.UNCONFIRMED, ItemStatus.INSUFFICIENT,
                             ItemStatus.UNKNOWN, ItemStatus.CONFLICT):
                if it.required or st.status != ItemStatus.UNCONFIRMED:
                    out.append((it.label, st.status))
        return out
