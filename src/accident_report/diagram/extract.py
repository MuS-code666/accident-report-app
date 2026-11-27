"""事故の説明文から作図用の構造化データを抜き出す（LLM担当部分）。"""

from __future__ import annotations

from accident_report.llm import OllamaClient
from accident_report.models.scene import DiagramSpec

SYSTEM_PROMPT = """あなたは事故状況図（上から見た平面図）を作るために、事故の説明文から位置関係を整理します。
座標は決めず、物どうしの位置関係・向き・動き・接触だけをJSONで返してください。

守ること:
- 説明文に書かれていることだけを使う。書かれていない位置は推測で決めず、unknowns に書く。
- entities: 図に描く人・重機・車両・設備・障害物。id は英数字（例: machine1, pillar1）。全場面で同じ id を使う。
  壁・柱・出入口・門扉・シャッター・コンベヤーなど動かない物は fixed を true にする。
- scenes: 事故の経過に合わせて2〜4場面（事故直前、事故発生時 など）。anchor は事故の中心になる重機・車両の id。
- relations: 「Aは Bの左にあった」なら subject=A, reference=B, direction=左。
  frame: 「重機から見て左」「進行方向の右」など基準物の向きで言っているなら「基準物の向き」、
  図面や方角で言っているなら「図の上」、どちらか分からなければ「不明」。
  distance: 当たっていた・接していたなら「接触」、すぐ近くなら「近い」、数m以上なら「離れている」、分からなければ「不明」。
- motions: その場面で行っている動き（前進・後退・左旋回・右旋回・停止）。距離が書いてあれば distance_m に数字で入れる。
- contacts: 事故発生時の接触。part はぶつかった側の部位。書かれていなければ「不明」。
- 文章から読み取れたことは certainty を「確定」、言い回しから推測したものは「未確定」にする。
- evidence には根拠になった文の番号（S1, S2 …）を入れる。"""


def number_sentences(text: str) -> str:
    """説明文を文ごとに番号付けする（根拠の参照用）。"""
    parts = [p.strip(" ・　") for p in text.replace("。", "。\n").splitlines()]
    parts = [p for p in parts if p]
    return "\n".join(f"S{i}: {p}" for i, p in enumerate(parts, 1))


def extract_diagram(client: OllamaClient, text: str, extra_qa: list[tuple[str, str]] | None = None) -> DiagramSpec:
    prompt = "# 事故の説明\n" + number_sentences(text)
    if extra_qa:
        prompt += "\n\n# 追加の聞き取り\n" + "\n".join(f"Q: {q}\nA: {a}" for q, a in extra_qa)
    return client.generate_json(SYSTEM_PROMPT, prompt, DiagramSpec, max_tokens=2500)
