"""Phase 1-C: 事故状況図の自動生成の検証。

「文章だけ」と「作図用の追加質問あり」の2通りで図を作り、比較する。

使い方:
  # 架空事例（採点つき）
  python prototypes/diagram/run.py --all
  # 実際のサンプル（ローカルのExcelから文章を読む。結果は data/ に保存、Git対象外）
  python prototypes/diagram/run.py --excel 事故報告書.xlsx --interactive
"""

from __future__ import annotations

import argparse
import copy
import fnmatch
import json
import math
import sys
import time
from datetime import datetime
from pathlib import Path

import yaml

from accident_report import PROJECT_ROOT
from accident_report.diagram.extract import extract_diagram
from accident_report.diagram.layout import DIR_ANGLE, layout, vec
from accident_report.diagram.questions import DONT_KNOW, find_questions
from accident_report.diagram.render import combined_svg, render_scenes, svg_to_png
from accident_report.llm import OllamaClient
from accident_report.models.scene import DiagramSpec

CASES_DIR = PROJECT_ROOT / "tests" / "fixtures" / "diagram"
OUT_DIR = PROJECT_ROOT / "data" / "results" / "diagram"


def resolve_choice(answer: str | None, choices: list[str]) -> str:
    if answer is None:
        return DONT_KNOW
    if answer in choices:
        return answer
    for c in choices:
        if answer in c:
            return c
    return DONT_KNOW


def ask_questions(spec: DiagramSpec, answers: dict | None, interactive: bool) -> list[dict]:
    """追加質問を最大2巡行い、回答を構造化データに反映する。"""
    asked: list[dict] = []
    done: set[str] = set()
    for _ in range(2):
        qs = [q for q in find_questions(spec, layout(spec)) if q.qid not in done]
        if not qs:
            break
        for q in qs:
            done.add(q.qid)
            if interactive:
                print(f"\nQ: {q.text}")
                for i, c in enumerate(q.choices, 1):
                    print(f"  {i}. {c}")
                raw = input("番号: ").strip()
                choice = q.choices[int(raw) - 1] if raw.isdigit() and 0 < int(raw) <= len(q.choices) else DONT_KNOW
            else:
                key = f"{q.qid}|{q.text}"
                ans = next((v for k, v in (answers or {}).items() if fnmatch.fnmatch(key, k)), None)
                choice = resolve_choice(ans, q.choices)
            q.apply(choice)
            asked.append({"qid": q.qid, "question": q.text, "answer": choice})
            print(f"  追加質問: {q.text} → {choice}")
    return asked


def direction_of(spec: DiagramSpec, lays, subj_kind: str, ref_kind: str) -> str | None:
    """レイアウト結果から、基準物の向きで見た方向（8方向）を求める。"""
    for lay in lays:
        subs = [e for e in lay.poses if spec.entity(e).kind == subj_kind]
        refs = [e for e in lay.poses if spec.entity(e).kind == ref_kind]
        if subs and refs:
            s, r = lay.poses[subs[0]], lay.poses[refs[0]]
            ang = math.degrees(math.atan2(s.x - r.x, -(s.y - r.y))) - r.heading
            ang = round((ang % 360) / 45) % 8 * 45
            return next(k for k, v in DIR_ANGLE.items() if v == ang)
    return None


def score(spec: DiagramSpec, lays, expected: dict) -> dict:
    kinds = {e.id: e.kind for e in spec.entities}
    as_list = lambda v: v if isinstance(v, list) else [v]  # noqa: E731
    checks = []
    for k in expected.get("entities", []):
        checks.append((f"登場: {k}", k in kinds.values()))
    for m in expected.get("motions", []):
        ok = any(kinds.get(x.entity) == m["entity"] and x.kind in as_list(m["kind"])
                 for s in spec.scenes for x in s.motions)
        checks.append((f"動き: {m['entity']} {m['kind']}", ok))
    for c in expected.get("contacts", []):
        ok = any(kinds.get(x.entity) == c["entity"] and kinds.get(x.target) == c["target"]
                 and x.part in as_list(c["part"]) for s in spec.scenes for x in s.contacts)
        checks.append((f"接触: {c['entity']}の{c['part']}→{c['target']}", ok))
    for r in expected.get("relations", []):
        d = direction_of(spec, lays, r["subject"], r["reference"])
        checks.append((f"位置: {r['subject']}は{r['reference']}の{r['direction']}（図では {d}）",
                       d in as_list(r["direction"])))
    passed = sum(ok for _, ok in checks)
    return {"score": f"{passed}/{len(checks)}", "checks": [{"item": n, "ok": ok} for n, ok in checks]}


def save(spec: DiagramSpec, name: str) -> tuple[list, Path]:
    lays = layout(spec)
    svgs = render_scenes(spec, lays)
    png = OUT_DIR / f"{name}.png"
    svg_to_png(combined_svg(svgs), png)
    (OUT_DIR / f"{name}.json").write_text(spec.model_dump_json(indent=2), encoding="utf-8")
    return lays, png


def run(case_id: str, text: str, client: OllamaClient, answers: dict | None, interactive: bool,
        expected: dict | None) -> dict:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    print(f"\n=== {case_id}（{client.model}）===")
    print("AIが事故の説明から位置関係を整理しています（2〜3分かかります。そのままお待ちください）…", flush=True)
    t = time.perf_counter()
    spec_a = extract_diagram(client, text)
    llm_s = time.perf_counter() - t
    lays_a, png_a = save(spec_a, f"{case_id}_A_text_only_{stamp}")
    qs_a = find_questions(spec_a, lays_a)
    print(f"[A 文章のみ] LLM {llm_s:.1f}秒  場面{len(spec_a.scenes)}  不足情報{len(qs_a)}件  → {png_a.name}")
    for lay in lays_a:
        for w in lay.warnings:
            print(f"   警告({lay.scene.phase}): {w}")
    if spec_a.unknowns:
        print(f"   AIが判断できなかったこと: {spec_a.unknowns}")

    spec_b = copy.deepcopy(spec_a)
    asked = ask_questions(spec_b, answers, interactive)
    lays_b, png_b = save(spec_b, f"{case_id}_B_with_questions_{stamp}")
    print(f"[B 追加質問あり] 質問{len(asked)}件  → {png_b.name}")
    for lay in lays_b:
        for w in lay.warnings:
            print(f"   警告({lay.scene.phase}): {w}")

    result = {"case": case_id, "model": client.model, "llm_seconds": round(llm_s, 1),
              "questions": asked, "png_a": str(png_a), "png_b": str(png_b),
              "warnings_a": [w for l in lays_a for w in l.warnings],
              "warnings_b": [w for l in lays_b for w in l.warnings],
              "unknowns": spec_a.unknowns}
    if expected:
        result["score_a"] = score(spec_a, lays_a, expected)
        result["score_b"] = score(spec_b, lays_b, expected)
        for label, key in (("A 文章のみ", "score_a"), ("B 追加質問あり", "score_b")):
            print(f"採点 {label}: {result[key]['score']}")
            for c in result[key]["checks"]:
                print(f"   {'○' if c['ok'] else '×'} {c['item']}")
    (OUT_DIR / f"{case_id}_result_{stamp}.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def read_excel_text(path: Path) -> str:
    import openpyxl
    ws = openpyxl.load_workbook(path).worksheets[1]
    return str(ws["B7"].value)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--case", type=Path)
    p.add_argument("--all", action="store_true")
    p.add_argument("--excel", type=Path)
    p.add_argument("--interactive", action="store_true")
    p.add_argument("--model", default="qwen3:8b")
    args = p.parse_args()

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    client = OllamaClient(model=args.model)
    print(f"AIモデル（{args.model}）を読み込んでいます…", flush=True)
    client.warm_up()
    if args.excel:
        run("real_sample", read_excel_text(args.excel), client, None, args.interactive, None)
        return 0
    paths = sorted(CASES_DIR.glob("*.yaml")) if args.all else [args.case]
    if paths == [None]:
        p.print_help()
        return 1
    for path in paths:
        case = yaml.safe_load(path.read_text(encoding="utf-8"))
        run(case["id"], case["text"], client, case.get("drawing_answers"), args.interactive,
            case.get("expected"))
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
