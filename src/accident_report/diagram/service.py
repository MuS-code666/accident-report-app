"""アプリから使う作図の流れ: 聞き取り → AIで位置関係を整理 → 選択式の追加質問 → 図（PNG）。

追加質問の回答は LLM を通さず、構造化データ（DiagramSpec）を直接書き換える。
保存する状態（DiagramState）は SQLite の cases.diagram_json に入れる。
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, Field

from accident_report.diagram.extract import extract_diagram
from accident_report.diagram.layout import Pose, SceneLayout, layout
from accident_report.diagram.questions import DrawingQuestion, find_questions
from accident_report.diagram.render import combined_svg, render_scenes, svg_to_png
from accident_report.interview.engine import InterviewEngine
from accident_report.llm import OllamaClient
from accident_report.models.scene import DiagramSpec

# 図に関係しない項目（原因・対策・事後の対応など）は作図の材料にしない
SKIP_ITEMS = {"cause", "countermeasure", "response_after", "work_instruction", "qualification",
              "human_damage", "reporter_role"}
EXCEL_MAX_PANELS = 2  # Excel の図の枠に入れる場面の数。3場面以上は全体を別紙に載せる


class DrawingAnswer(BaseModel):
    qid: str
    question: str
    choice: str


class ManualPose(BaseModel):
    """利用者が手で直した位置と向き（m・度。0 = 図の上、時計回り）。"""
    x: float
    y: float
    heading: float


class DiagramState(BaseModel):
    spec: DiagramSpec
    answers: list[DrawingAnswer] = Field(default_factory=list)
    # 手で直した位置（場面ID → 物のID → 位置）と、図から外した物（場面ID → 物のID）
    manual: dict[str, dict[str, ManualPose]] = Field(default_factory=dict)
    hidden: dict[str, list[str]] = Field(default_factory=dict)
    main_png: str | None = None  # Excel の図の枠に貼る図
    extra_png: str | None = None  # 3場面以上のとき、別紙に貼る全場面の図
    accepted: bool = False  # 利用者が「この図を使う」を押した

    def asked(self) -> set[str]:
        return {a.qid for a in self.answers}


def diagram_text(engine: InterviewEngine) -> str:
    """聞き取りの回答から、作図の材料になる説明文を作る（質問文も付けて意味が分かるようにする）。"""
    lines = []
    for a in engine.answers:
        if a.question_item_id in SKIP_ITEMS:
            continue
        text = a.text.strip()
        if not text.endswith(("。", "？", "！")):
            text += "。"
        lines.append(text if a.question_item_id is None else f"（{a.question_text}）{text}")
    return "\n".join(lines)


def create_state(client: OllamaClient, engine: InterviewEngine) -> DiagramState:
    """AIで位置関係を整理する（時間がかかるのでバックグラウンドで呼ぶ）。"""
    return DiagramState(spec=extract_diagram(client, diagram_text(engine)))


def pending_questions(state: DiagramState) -> list[DrawingQuestion]:
    """まだ聞いていない追加質問。回答のたびに作り直す（前の回答で不要になった質問は出さない）。"""
    asked = state.asked()
    return [q for q in find_questions(state.spec, layout(state.spec)) if q.qid not in asked]


def answer(state: DiagramState, question: DrawingQuestion, choice: str) -> None:
    question.apply(choice)
    state.answers.append(DrawingAnswer(qid=question.qid, question=question.text, choice=choice))


def final_layouts(state: DiagramState) -> list[SceneLayout]:
    """自動レイアウトに、利用者の手直し（移動・回転・図から外す）を重ねる。"""
    lays = layout(state.spec)
    for lay in lays:
        sid = lay.scene.scene_id
        movers = {c.entity for c in lay.scene.contacts}
        for eid, mp in state.manual.get(sid, {}).items():
            old = lay.poses.get(eid)
            if old is not None and eid in movers:
                # 接触の印は、ぶつかった側と一緒に動かす
                dx, dy = mp.x - old.x, mp.y - old.y
                lay.contact_points = [(x + dx, y + dy) for x, y in lay.contact_points]
            lay.poses[eid] = Pose(mp.x, mp.y, mp.heading, certain=True)
            if eid in lay.unplaced:
                lay.unplaced.remove(eid)
        for eid in state.hidden.get(sid, []):
            lay.poses.pop(eid, None)
            if eid in lay.unplaced:
                lay.unplaced.remove(eid)
    return lays


def excel_scenes(layouts: list[SceneLayout]) -> list[int]:
    """Excel の枠に入れる場面: 2場面まではすべて。3場面以上は「事故発生時」とその直前。"""
    if len(layouts) <= EXCEL_MAX_PANELS:
        return list(range(len(layouts)))
    hit = next((i for i, lay in enumerate(layouts) if lay.scene.phase == "事故発生時"), len(layouts) - 1)
    start = max(0, hit - 1)
    return [start, start + 1]


def render(state: DiagramState, out_dir: Path, name: str) -> tuple[Path, Path | None, list[str]]:
    """図を描いて PNG に保存する。(Excel用の図, 別紙用の図 or None, 警告) を返す。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    lays = final_layouts(state)
    panels = render_scenes(state.spec, lays)
    main = out_dir / f"{name}_main.png"
    svg_to_png(combined_svg([panels[i] for i in excel_scenes(lays)]), main)
    extra = None
    if len(panels) > EXCEL_MAX_PANELS:
        extra = out_dir / f"{name}_all.png"
        svg_to_png(combined_svg(panels), extra)
    state.main_png, state.extra_png = str(main), (str(extra) if extra else None)
    label = lambda eid: (state.spec.entity(eid).label if state.spec.entity(eid) else eid)  # noqa: E731
    ids = sorted((e.id for e in state.spec.entities), key=len, reverse=True)

    def readable(w: str) -> str:  # 警告の中の id を名前に直す
        for eid in ids:
            w = w.replace(eid, label(eid))
        return w

    warnings = [f"{lay.scene.phase}: {readable(w)}" for lay in lays for w in lay.warnings]
    warnings += [f"{lay.scene.phase}: 位置が決まらず描けなかった物 {'、'.join(label(e) for e in lay.unplaced)}"
                 for lay in lays if lay.unplaced]
    return main, extra, warnings
