"""作図部品の定義（上から見た形）。

各部品は「長さ（向いている方向）× 幅」の大きさ[m]と、SVGの描画関数を持つ。
ローカル座標は部品の中心が原点、前方が -y（図の上）。
Phase 1 はコードで描く簡易な線画。Phase 3 で SVG ファイルの部品に置き換える。
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

INK = "#222222"
FILL = "#ffffff"
DARK = "#888888"


@dataclass(frozen=True)
class Part:
    length: float  # 向いている方向の長さ [m]
    width: float  # 横幅 [m]
    draw: Callable[[float, float], str]  # (長さpx, 幅px) -> SVG要素（中心原点・前方が上）
    movable: bool = True


def _rect(w, h, x=None, y=None, fill=FILL, stroke=INK, sw=1.5, dash=None, rx=0):
    x = -w / 2 if x is None else x
    y = -h / 2 if y is None else y
    d = f' stroke-dasharray="{dash}"' if dash else ""
    return (f'<rect x="{x:.1f}" y="{y:.1f}" width="{w:.1f}" height="{h:.1f}" rx="{rx}" '
            f'fill="{fill}" stroke="{stroke}" stroke-width="{sw}"{d}/>')


def _line(x1, y1, x2, y2, sw=1.5, stroke=INK):
    return f'<line x1="{x1:.1f}" y1="{y1:.1f}" x2="{x2:.1f}" y2="{y2:.1f}" stroke="{stroke}" stroke-width="{sw}"/>'


def person(L, W):
    r = min(L, W) / 2
    return (f'<circle cx="0" cy="0" r="{r:.1f}" fill="{FILL}" stroke="{INK}" stroke-width="1.5"/>'
            f'<path d="M{-r * 0.5:.1f} {-r * 0.6:.1f} L0 {-r * 1.5:.1f} L{r * 0.5:.1f} {-r * 0.6:.1f}" '
            f'fill="{INK}"/>')


def guide(L, W):
    r = min(L, W) / 2
    return person(L, W) + f'<rect x="{r * 0.6:.1f}" y="{-r * 1.6:.1f}" width="{r * 0.9:.1f}" height="{r * 0.6:.1f}" fill="{DARK}"/>'


def forklift(L, W):
    body_l = L * 0.7
    top = -L / 2 + L * 0.3  # フォーク部分を除いた車体の前端
    s = _rect(W, body_l, y=top)
    s += _rect(W * 0.6, body_l * 0.35, y=top + body_l * 0.15, fill="#eeeeee")  # 運転席
    s += _rect(W, body_l * 0.2, y=top + body_l * 0.8, fill=DARK)  # カウンターウェイト（後部）
    s += _line(-W * 0.3, -L / 2, -W * 0.3, top, sw=3) + _line(W * 0.3, -L / 2, W * 0.3, top, sw=3)
    return s


def excavator(L, W):
    # クローラー（左右）、上部旋回体、キャビン（左前）、アームとバケット（前）
    body_l = L * 0.65
    top = -L / 2 + L * 0.35
    s = _rect(W * 0.25, body_l, x=-W / 2, y=top, fill=DARK)
    s += _rect(W * 0.25, body_l, x=W / 2 - W * 0.25, y=top, fill=DARK)
    s += _rect(W * 0.8, body_l * 0.8, y=top + body_l * 0.1, rx=4)
    s += _rect(W * 0.3, body_l * 0.3, x=-W * 0.38, y=top + body_l * 0.12, fill="#eeeeee")  # キャビン
    s += _line(W * 0.1, top + body_l * 0.15, W * 0.1, -L / 2 + L * 0.08, sw=4)  # アーム
    s += _rect(W * 0.45, L * 0.1, x=W * 0.1 - W * 0.225, y=-L / 2, fill=DARK)  # バケット
    return s


def wheel_loader(L, W):
    s = _rect(W * 0.9, L * 0.75, y=-L / 2 + L * 0.2, rx=3)
    s += _rect(W, L * 0.15, y=-L / 2, fill=DARK)  # バケット
    s += _rect(W * 0.5, L * 0.2, y=-L / 2 + L * 0.4, fill="#eeeeee")
    return s


def truck(L, W):
    s = _rect(W, L * 0.25, y=-L / 2, fill="#eeeeee")  # キャブ
    s += _rect(W, L * 0.72, y=-L / 2 + L * 0.28)  # 荷台
    return s


def box(label_fill=FILL):
    def draw(L, W):
        return _rect(W, L, fill=label_fill)
    return draw


def pillar(L, W):
    return _rect(W, L, fill="#555555")


def wall(L, W):
    return _rect(W, L, fill="#777777", sw=1)


def entrance(L, W):
    # 開口部（左右に柱）: 長さ方向が通り抜ける方向、幅方向が開口の幅
    p = W * 0.12
    s = _rect(W, L, fill="none", stroke=DARK, sw=1, dash="4 3")
    s += _rect(p, L, x=-W / 2, fill="#555555") + _rect(p, L, x=W / 2 - p, fill="#555555")
    return s


def shutter(L, W):
    s = _rect(W, L, fill="#dddddd")
    n = 6
    for i in range(1, n):
        x = -W / 2 + W * i / n
        s += _line(x, -L / 2, x, L / 2, sw=0.8)
    return s


def round_obj(fill=FILL):
    def draw(L, W):
        r = min(L, W) / 2
        return f'<circle cx="0" cy="0" r="{r:.1f}" fill="{fill}" stroke="{INK}" stroke-width="1.5"/>'
    return draw


def pallet(L, W):
    s = _rect(W, L)
    for f in (-0.3, 0, 0.3):
        s += _line(-W / 2, L * f, W / 2, L * f, sw=0.8)
    return s


def conveyor(L, W):
    s = _rect(W, L)
    n = max(3, int(L / 6))
    for i in range(1, n):
        y = -L / 2 + L * i / n
        s += _line(-W / 2, y, W / 2, y, sw=0.8)
    return s


def waste(L, W):
    return (f'<path d="M{-W / 2:.1f} {L * 0.2:.1f} L{-W * 0.2:.1f} {-L / 2:.1f} L{W * 0.3:.1f} {-L * 0.3:.1f} '
            f'L{W / 2:.1f} {L * 0.3:.1f} L0 {L / 2:.1f} Z" fill="#eeeeee" stroke="{INK}" stroke-width="1.2"/>')


PARTS: dict[str, Part] = {
    "作業員": Part(0.6, 0.6, person),
    "誘導員": Part(0.6, 0.6, guide),
    "フォークリフト": Part(3.6, 1.2, forklift),
    "トラック": Part(7.0, 2.3, truck),
    "油圧ショベル": Part(6.0, 2.6, excavator),
    "ホイールローダー": Part(5.5, 2.3, wheel_loader),
    "その他重機": Part(5.0, 2.4, box("#eeeeee")),
    "コンベヤー": Part(6.0, 1.0, conveyor, movable=False),
    "破砕機": Part(4.0, 3.0, box("#eeeeee"), movable=False),
    "選別機": Part(4.0, 3.0, box("#eeeeee"), movable=False),
    "廃棄物": Part(1.2, 1.2, waste),
    "荷物": Part(1.0, 1.0, box()),
    "パレット": Part(1.1, 1.1, pallet),
    "コンテナ": Part(2.0, 1.5, box()),
    "バケツ": Part(0.6, 0.6, round_obj()),
    "フレコン": Part(1.0, 1.0, round_obj("#eeeeee")),
    "壁": Part(0.3, 8.0, wall, movable=False),
    "柱": Part(0.5, 0.5, pillar, movable=False),
    "出入口": Part(0.6, 4.0, entrance, movable=False),
    "シャッター": Part(0.3, 4.0, shutter, movable=False),
    "門扉": Part(0.3, 3.0, shutter, movable=False),
}

# 接触部位 → ローカル座標上の点（長さ・幅に対する比率、前方が -y）
PART_POINTS: dict[str, tuple[float, float]] = {
    "前部": (0, -0.5), "後部": (0, 0.5), "左側": (-0.5, 0), "右側": (0.5, 0),
    "左前": (-0.5, -0.5), "右前": (0.5, -0.5), "左後": (-0.5, 0.5), "右後": (0.5, 0.5),
    "バケット": (0.04, -0.5), "アーム": (0.04, -0.3), "キャビン": (-0.4, -0.1),
    "クローラー": (-0.5, 0), "フォーク": (0, -0.5), "不明": (0, 0),
}
