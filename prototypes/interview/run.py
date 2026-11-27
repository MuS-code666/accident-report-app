"""Phase 1-B: AIインタビューの検証。

使い方:
  # 架空事例で自動インタビュー（模擬回答者）し、指標を出す
  python prototypes/interview/run.py --case tests/fixtures/cases/case01_forklift_shutter.yaml
  python prototypes/interview/run.py --all --model qwen3:4b --writer-model qwen3:8b

  # キーボードで対話（/dk=分からない /na=該当なし /later=後で /end=終了）
  python prototypes/interview/run.py --interactive

結果は data/results/ に JSON で保存する（Git対象外）。
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from accident_report import PROJECT_ROOT
from accident_report.interview.engine import InterviewEngine
from accident_report.interview.extraction import extract
from accident_report.interview.questions import load_question_set
from accident_report.llm import OllamaClient
from accident_report.writing.notice import check_draft, generate_notice

CASES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "cases"
RESULTS_DIR = PROJECT_ROOT / "data" / "results"


def make_engine(client: OllamaClient) -> InterviewEngine:
    def extractor(items, known, types, question, answer):
        return extract(client, items, known, types, question, answer)

    return InterviewEngine(load_question_set(), extractor)


def respond(case: dict, q) -> str:
    if q.kind == "opening":
        return case["opening"]
    if q.kind == "conflict":
        return case.get("conflict_answers", {}).get(q.item_id, "前に答えたほうが正しいです。")
    if q.kind == "deepen":
        deep = case.get("deepen_answers", {})
        if q.item_id in deep:
            return deep[q.item_id]
        return case.get("answers", {}).get(q.item_id, "それ以上は分かりません。")
    return case.get("answers", {}).get(q.item_id, "分からないです。")


def run_case(path: Path, model: str, writer_model: str) -> dict:
    case = yaml.safe_load(path.read_text(encoding="utf-8"))
    client = OllamaClient(model=model)
    writer = client if writer_model == model else OllamaClient(model=writer_model)
    client.warm_up()
    eng = make_engine(client)

    print(f"\n=== {case['id']} {case['title']}（抽出 {model} / 文章 {writer_model}）===")
    transcript = []
    asked_items: list[str] = []
    reasked_answered = 0
    t0 = time.perf_counter()
    while (q := eng.next_question()) is not None:
        if q.item_id:
            if eng.states[q.item_id].status.value == "回答済み" and q.kind == "item":
                reasked_answered += 1
            asked_items.append(q.item_id)
        a = respond(case, q)
        n_calls = len(client.history)
        t = time.perf_counter()
        eng.submit_answer(a)
        wait = time.perf_counter() - t
        print(f"Q[{q.kind}] {q.text}\nA: {a}\n   (解析 {wait:.1f}秒, LLM呼び出し {len(client.history) - n_calls}回)")
        transcript.append({"kind": q.kind, "item": q.item_id, "q": q.text, "a": a, "wait_s": round(wait, 1)})
        if len(transcript) > 80:
            print("!! 質問が多すぎるため打ち切り")
            break
    interview_s = time.perf_counter() - t0

    # 抽出の正解率
    hits, misses = [], []
    for item_id, keywords in case.get("expected", {}).items():
        st = eng.states.get(item_id)
        value = st.value if st else None
        ok = bool(value) and all(any(alt in value for alt in k.split("|")) for k in keywords)
        (hits if ok else misses).append({"item": item_id, "value": value, "expected": keywords})

    # 文章生成
    t = time.perf_counter()
    draft = generate_notice(writer, eng)
    writing_s = time.perf_counter() - t
    issues = check_draft(draft, eng)
    report_text = " ".join(s.text for sec in ("overview", "cause", "countermeasure")
                           for s in getattr(draft, sec))
    forbidden = [w for w in case.get("forbidden_in_report", []) if w in report_text]

    waits = [x["wait_s"] for x in transcript if x["kind"] != "opening"]
    dup = len(asked_items) - len(set(asked_items))
    result = {
        "case": case["id"],
        "model": model,
        "writer_model": writer_model,
        "run_at": datetime.now().isoformat(timespec="seconds"),
        "questions": len(transcript),
        "repeated_items": dup,
        "reasked_answered_items": reasked_answered,
        "types_detected": eng.active_types,
        "types_expected": case.get("expected_types", []),
        "extraction_accuracy": f"{len(hits)}/{len(hits) + len(misses)}",
        "misses": misses,
        "wait_per_answer_s": {"mean": round(statistics.mean(waits), 1) if waits else 0,
                              "max": max(waits) if waits else 0},
        "interview_llm_total_s": round(interview_s, 1),
        "writing_s": round(writing_s, 1),
        "check_issues": [i.model_dump() for i in issues],
        "forbidden_words_in_report": forbidden,
        "unresolved": [f"{label}（{st.value}）" for label, st in eng.unresolved()],
        "draft": draft.model_dump(),
        "item_states": {k: v.model_dump(mode="json") for k, v in eng.states.items()},
        "transcript": transcript,
        "log": eng.log,
    }
    print_summary(result)
    return result


def print_summary(r: dict) -> None:
    print("\n--- 結果 ---")
    for key in ("questions", "repeated_items", "reasked_answered_items", "types_detected",
                "types_expected", "extraction_accuracy", "wait_per_answer_s",
                "interview_llm_total_s", "writing_s", "forbidden_words_in_report", "unresolved"):
        print(f"{key}: {r[key]}")
    for m in r["misses"]:
        print(f"  抽出ミス {m['item']}: {m['value']!r}（期待 {m['expected']}）")
    for i in r["check_issues"]:
        print(f"  チェック指摘 [{i['section']}] {i['problem']}: {i['text']}")
    d = r["draft"]
    print(f"\n作業区分: {d['work_category']} {d.get('work_category_other') or ''}")
    print("事故概要:")
    for s in d["overview"]:
        print(f"  ・{s['text']}  {s['evidence']}")
    print("発生原因:")
    for s in d["cause"]:
        print(f"  ・{s['text']}  {s['evidence']}")
    print("対策案:")
    for s in d["countermeasure"]:
        print(f"  ・{s['text']}  {s['evidence']}")


def interactive(model: str, writer_model: str) -> None:
    client = OllamaClient(model=model)
    client.warm_up()
    eng = make_engine(client)
    buttons = {"/dk": "dont_know", "/na": "not_applicable", "/later": "later"}
    while (q := eng.next_question()) is not None:
        print(f"\nQ: {q.text}")
        a = input("A: ").strip()
        if a == "/end":
            break
        if a in buttons:
            eng.mark_button(buttons[a])
            continue
        t = time.perf_counter()
        eng.submit_answer(a)
        print(f"   (解析 {time.perf_counter() - t:.1f}秒)")
    writer = OllamaClient(model=writer_model)
    draft = generate_notice(writer, eng)
    print(json.dumps(draft.model_dump(), ensure_ascii=False, indent=2))
    for i in check_draft(draft, eng):
        print("チェック指摘:", i.problem, i.text)
    print("未確認事項:", eng.unresolved())


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=Path)
    p.add_argument("--all", action="store_true")
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--model", default="qwen3:8b")
    p.add_argument("--writer-model", default=None)
    args = p.parse_args()
    writer_model = args.writer_model or args.model

    if args.interactive:
        interactive(args.model, writer_model)
        return 0
    paths = sorted(CASES_DIR.glob("*.yaml")) if args.all else [args.case]
    if not paths or paths == [None]:
        p.print_help()
        return 1
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    for path in paths:
        r = run_case(path, args.model, writer_model)
        out = RESULTS_DIR / f"interview_{r['case']}_{args.model.replace(':', '-')}_{stamp}.json"
        out.write_text(json.dumps(r, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"保存: {out}")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
