"""事故の説明文から作図用の構造化データを抜き出す（LLM担当部分）と、その点検（Python側）。"""

from __future__ import annotations

import re

from accident_report.llm import OllamaClient
from accident_report.models.scene import DiagramSpec

SYSTEM_PROMPT = """あなたは事故状況図（上から見た平面図）を作るために、事故の説明文から位置関係を整理します。
座標は決めず、物どうしの位置関係・向き・動き・接触だけをJSONで返してください。

守ること:
- 説明文に書かれていることだけを使う。書かれていない位置関係は作らず、unknowns に書く。
- entities: 図に描く人・重機・車両・設備・障害物。id は英数字（例: machine1, pillar1）。全場面で同じ id を使う。
  壁・柱・出入口・門扉・シャッター・コンベヤーなど動かない物は fixed を true にする。
  重機や車両を運転している人は車内にいるので entities に入れない。誘導している人は kind を「誘導員」にする。
- scenes: 事故の経過に合わせて2〜4場面（事故直前、事故発生時 など）。anchor は事故の中心になる重機・車両の id。
- relations: 「Aは Bの左にあった」なら subject=A, reference=B, direction=左。
  frame: 「重機の左後ろ」「進行方向の右」「運転席から見て左」のように、物の向きを基準に言っているなら「基準物の向き」。
  「北側」「図面の上」のように方角や図面で言っているときだけ「図の上」。どちらか分からなければ「不明」。
  distance: 当たっていた・接していたなら「接触」、すぐ近くなら「近い」、数m以上なら「離れている」、分からなければ「不明」。
- motions: その場面で行っている動き（前進・後退・左旋回・右旋回・停止）。距離が書いてあれば distance_m に数字で入れる。
- contacts: 接触は「事故発生時」の場面にだけ書く。part はぶつかった側の部位。書かれていなければ「不明」。
- 文章から読み取れたことは certainty を「確定」、言い回しから推測したものは「未確定」にする。
- evidence には根拠になった文の番号（S1, S2 …）を入れる。"""

COMPASS = re.compile(r"[東西南北]側|[東西南北]の方|図面|図の上|図の下|地図")
GUIDE_WORDS = ("誘導",)
OPPOSITE = {"前": "後", "後": "前", "左": "右", "右": "左", "左前": "右後", "右後": "左前", "右前": "左後", "左後": "右前"}
MACHINE_HINTS = [
    (r"フォーク", "フォークリフト"), (r"ローダー", "ホイールローダー"),
    (r"ショベル|ユンボ|バックホウ|キャタピラ|クローラ|バケット|重機", "油圧ショベル"),
    (r"トラック|ダンプ", "トラック"),
]
SUFFIXES = ("作業員", "さん", "の", "係長", "職長", "班長")


def number_sentences(text: str) -> list[str]:
    """説明文を文に分ける（根拠の参照用に S1, S2 … と番号を付けて渡す）。"""
    parts = [p.strip(" ・　") for p in text.replace("。", "。\n").splitlines()]
    return [p for p in parts if p]


def extract_diagram(client: OllamaClient, text: str, extra_qa: list[tuple[str, str]] | None = None) -> DiagramSpec:
    sentences = number_sentences(text)
    prompt = "# 事故の説明\n" + "\n".join(f"S{i}: {s}" for i, s in enumerate(sentences, 1))
    if extra_qa:
        prompt += "\n\n# 追加の聞き取り\n" + "\n".join(f"Q: {q}\nA: {a}" for q, a in extra_qa)
    spec = client.generate_json(SYSTEM_PROMPT, prompt, DiagramSpec, max_tokens=2500)
    return review_spec(spec, sentences)


def _keywords(spec: DiagramSpec, eid: str) -> list[str]:
    e = spec.entity(eid)
    words = {e.kind, e.label}
    core = e.label
    for s in SUFFIXES:
        core = core.replace(s, "")
    if len(core) >= 2:
        words.add(core)
    if e.kind == "フォークリフト":
        words.add("フォーク")
    if e.kind in ("油圧ショベル", "ホイールローダー", "その他重機"):
        words.add("重機")
    return [w for w in words if w]


def review_spec(spec: DiagramSpec, sentences: list[str]) -> DiagramSpec:
    """AIの出力を点検・補正する。補正した内容は unknowns の末尾に記録する。"""
    notes: list[str] = []
    text = "".join(sentences)
    sentence_of = {f"S{i}": s for i, s in enumerate(sentences, 1)}

    # 誘導している人は「誘導員」の部品にする
    for e in spec.entities:
        if e.kind == "作業員":
            names = [w for w in _keywords(spec, e.id) if w not in ("作業員",)]
            if any(f"誘導員の{n}" in text or f"{n}が誘導" in text or f"{n}は誘導" in text for n in names):
                e.kind = "誘導員"
                notes.append(f"{e.label} を誘導員にした")

    # 誘導している言葉がないのに「誘導員」になっている人は、作業員に戻す
    for e in spec.entities:
        if e.kind == "誘導員" and not any(w in text for w in GUIDE_WORDS):
            e.kind = "作業員"
            notes.append(f"{e.label} は誘導の記述がないため作業員にした")

    # 図の中心（anchor）が人になっていたら、文中の言葉から重機・車両の種類に直す
    for eid in {sc.anchor for sc in spec.scenes}:
        e = spec.entity(eid)
        if e and e.kind in ("作業員", "誘導員"):
            kind = next((k for pat, k in MACHINE_HINTS if re.search(pat, text)), "その他重機")
            notes.append(f"{e.label}（{e.kind}）を図の中心の{kind}に直した")
            e.kind, e.fixed = kind, False

    # 重機・車両の名前が運転者名になっていたら、部品の種類名にする
    for e in spec.entities:
        if e.kind not in ("作業員", "誘導員") and any(e.label.endswith(x) for x in ("作業員", "さん", "係長", "職長")):
            notes.append(f"{e.label} は人名のため、名前を「{e.kind}」にした")
            e.label = e.kind

    # 動く物（重機・車両）を主語、固定物を基準にした関係は向きを反転して「固定物は重機の◯◯」に直す
    movers = {s.anchor for s in spec.scenes}
    for scene in spec.scenes:
        for rel in scene.relations:
            subj, ref = spec.entity(rel.subject), spec.entity(rel.reference)
            if subj and ref and rel.subject in movers and rel.reference not in movers:
                rel.subject, rel.reference = rel.reference, rel.subject
                rel.direction = OPPOSITE[rel.direction]
                if rel.frame == "基準物の向き":
                    rel.frame = "不明"  # 固定物の「向き」は決まらないので確認する
                notes.append(f"{rel.subject}→{rel.reference} の主語と基準を入れ替えた")

    # 図に関わらない人（位置関係・接触・動きのどれにも出てこない作業員）は描かない
    used = {x for sc in spec.scenes for r in sc.relations for x in (r.subject, r.reference)}
    used |= {x for sc in spec.scenes for c in sc.contacts for x in (c.entity, c.target)}
    used |= {m.entity for sc in spec.scenes for m in sc.motions} | movers
    dropped = [e for e in spec.entities if e.kind == "作業員" and e.id not in used]
    if dropped:
        spec.entities = [e for e in spec.entities if e not in dropped]
        notes.append("図に関係しない人物を除外: " + "、".join(e.label for e in dropped))

    for scene in spec.scenes:
        for rel in scene.relations:
            # 方角や図面の言葉がないのに「図の上」基準 → 基準は不明として確認する
            if rel.frame == "図の上" and not COMPASS.search(text):
                rel.frame = "不明"
                notes.append(f"{rel.subject}→{rel.reference} の左右の基準を確認する")
            # 根拠の文に両方の物が出てこない位置関係は未確定にする
            cited = " ".join(sentence_of.get(ev, "") for ev in rel.evidence) or text
            ok = (any(w in cited for w in _keywords(spec, rel.subject))
                  and any(w in cited for w in _keywords(spec, rel.reference)))
            if not ok and rel.certainty == "確定":
                rel.certainty = "未確定"
                notes.append(f"{rel.subject}→{rel.reference} は文中に根拠がないため未確定")

    # 「固定物が重機に当たった」という逆向きの接触は、「重機が固定物に当たった」にそろえる
    fixed = {e.id for e in spec.entities if e.fixed}
    for scene in spec.scenes:
        kept = []
        for c in scene.contacts:
            if c.entity in fixed and c.target not in fixed:
                if any(k.entity == c.target and k.target == c.entity for k in scene.contacts):
                    notes.append(f"{c.entity}→{c.target} の接触は重複のため削除")
                    continue
                c.entity, c.target, c.part = c.target, c.entity, "不明"
                notes.append(f"{c.target}→{c.entity} の接触を、動く側からの接触に直した")
            kept.append(c)
        scene.contacts = kept

    # 接触は「事故発生時」の場面にだけ置く
    contact_scenes = [s for s in spec.scenes if s.phase == "事故発生時" and s.contacts]
    if contact_scenes:
        keep = contact_scenes[0]
        for s in spec.scenes:
            if s is not keep and s.contacts:
                s.contacts = []
                notes.append(f"{s.phase} の接触を削除（事故発生時に一本化）")

    spec.unknowns += [f"［点検］{n}" for n in dict.fromkeys(notes)]
    return spec
