"""レイアウト結果を SVG に描き、PNG に変換する。

画面表示と Excel 用 PNG は同じ SVG から作るので、見た目がずれない。
"""

from __future__ import annotations

import math
import os
from pathlib import Path
from xml.sax.saxutils import escape

from accident_report.diagram.layout import DEFAULT_MOVE, SceneLayout, corners, vec
from accident_report.diagram.parts import INK, PARTS
from accident_report.models.scene import DiagramSpec

FONT = "Yu Gothic UI, Meiryo, sans-serif"
PANEL_W, PANEL_H = 420, 330
MARGIN = 36
ACCENT = "#c62828"


def _bounds(spec: DiagramSpec, layouts: list[SceneLayout]) -> tuple[float, float, float, float]:
    xs, ys = [], []
    for lay in layouts:
        for eid, p in lay.poses.items():
            for x, y in corners(spec, eid, p):
                xs.append(x)
                ys.append(y)
            for m in lay.scene.motions:
                if m.entity == eid:
                    fx, fy = vec(p.heading)
                    d = (m.distance_m or DEFAULT_MOVE) + 1
                    xs += [p.x + fx * d, p.x - fx * d]
                    ys += [p.y + fy * d, p.y - fy * d]
    if not xs:
        return -5, -5, 5, 5
    return min(xs), min(ys), max(xs), max(ys)


def scene_svg(spec: DiagramSpec, lay: SceneLayout, bounds, title: str | None = None) -> str:
    x0, y0, x1, y1 = bounds
    usable_w, usable_h = PANEL_W - 2 * MARGIN, PANEL_H - 2 * MARGIN - 30
    scale = min(usable_w / max(x1 - x0, 1), usable_h / max(y1 - y0, 1))
    ox = MARGIN + (usable_w - (x1 - x0) * scale) / 2 - x0 * scale
    oy = MARGIN + 18 + (usable_h - (y1 - y0) * scale) / 2 - y0 * scale

    def P(x, y):
        return ox + x * scale, oy + y * scale

    out = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{PANEL_W}" height="{PANEL_H}" '
           f'viewBox="0 0 {PANEL_W} {PANEL_H}" font-family="{FONT}">',
           f'<rect x="0" y="0" width="{PANEL_W}" height="{PANEL_H}" fill="#ffffff"/>',
           f'<text x="12" y="22" font-size="14" font-weight="bold" fill="{INK}">'
           f'{escape(title or lay.scene.phase)}</text>']

    # 固定物 → 動く物の順に描く
    order = sorted(lay.poses, key=lambda e: (not spec.entity(e).fixed, e))
    for eid in order:
        p = lay.poses[eid]
        ent = spec.entity(eid)
        part = PARTS[ent.kind]
        cx, cy = P(p.x, p.y)
        out.append(f'<g transform="translate({cx:.1f},{cy:.1f}) rotate({p.heading:.1f})">'
                   f'{part.draw(part.length * scale, part.width * scale)}</g>')
        if not p.certain:
            pts = " ".join(f"{a:.1f},{b:.1f}" for a, b in (P(x, y) for x, y in corners(spec, eid, p)))
            out.append(f'<polygon points="{pts}" fill="none" stroke="{ACCENT}" stroke-width="1" '
                       f'stroke-dasharray="3 3"/>')

    # 動きの矢印
    for m in lay.scene.motions:
        if m.entity not in lay.poses or m.kind == "停止":
            continue
        p = lay.poses[m.entity]
        L = PARTS[spec.entity(m.entity).kind].length
        fx, fy = vec(p.heading)
        if m.kind in ("前進", "後退"):
            sgn = 1 if m.kind == "前進" else -1
            d = m.distance_m or DEFAULT_MOVE
            sx, sy = P(p.x + sgn * fx * L * 0.55, p.y + sgn * fy * L * 0.55)
            ex, ey = P(p.x + sgn * fx * (L * 0.55 + d), p.y + sgn * fy * (L * 0.55 + d))
            out.append(_arrow(sx, sy, ex, ey))
            label = m.kind + (f" 約{m.distance_m:g}m" if m.distance_m else "")
            out.append(_label(ex + 6, ey, label, ACCENT, 11))
        else:
            cx, cy = P(p.x, p.y)
            r = L * scale * 0.6
            sweep = 1 if m.kind == "右旋回" else 0
            a0 = math.radians(p.heading - 90 - 30)
            a1 = math.radians(p.heading - 90 + (30 if sweep else -60))
            sx, sy = cx + r * math.cos(a0), cy + r * math.sin(a0)
            ex, ey = cx + r * math.cos(a1), cy + r * math.sin(a1)
            out.append(f'<path d="M{sx:.1f} {sy:.1f} A{r:.1f} {r:.1f} 0 0 {sweep} {ex:.1f} {ey:.1f}" '
                       f'fill="none" stroke="{ACCENT}" stroke-width="2"/>')
            out.append(_label(ex + 4, ey, m.kind, ACCENT, 11))

    # 接触箇所
    for x, y in lay.contact_points:
        out.append(_star(*P(x, y)))

    # 名前と注釈（ラベルどうしが重ならない位置を下→上→右→左の順に探す）
    taken: list[tuple[float, float, float, float]] = []
    for eid in order:  # 図形そのものの範囲も避ける
        pts = [P(x, y) for x, y in corners(spec, eid, lay.poses[eid])]
        taken.append((min(a for a, _ in pts), min(b for _, b in pts),
                      max(a for a, _ in pts), max(b for _, b in pts)))
    for x, y in lay.contact_points:
        sx, sy = P(x, y)
        taken.append((sx - 12, sy - 12, sx + 12, sy + 12))
    for eid in order:
        p = lay.poses[eid]
        ent = spec.entity(eid)
        cx, cy = P(p.x, p.y)
        half = max(PARTS[ent.kind].length, PARTS[ent.kind].width) * scale / 2
        text = ent.label + ("（位置未確認）" if not p.certain else "")
        w = 11 * len(text)
        candidates = [(cx, cy + half + 13), (cx, cy - half - 6), (cx + half + 6 + w / 2, cy + 4),
                      (cx - half - 6 - w / 2, cy + 4), (cx, cy + half + 27)]
        for lx, ly in candidates:
            box_ = (lx - w / 2, ly - 11, lx + w / 2, ly + 2)
            if not any(box_[0] < b[2] and b[0] < box_[2] and box_[1] < b[3] and b[1] < box_[3] for b in taken):
                break
        taken.append(box_)
        out.append(_label(lx, ly, text, INK, 11, anchor="middle"))
    for n in lay.scene.notes:
        if n.target in lay.poses:
            cx, cy = P(lay.poses[n.target].x, lay.poses[n.target].y)
            out.append(_label(cx + 14, cy - 14, n.text, "#1565c0", 10))

    foot = "縮尺なし（模式図）"
    if lay.unplaced:
        foot += "　未配置: " + "、".join(spec.entity(e).label for e in lay.unplaced)
    out.append(_label(12, PANEL_H - 10, foot, "#555555", 10))
    out.append("</svg>")
    return "\n".join(out)


def _arrow(sx, sy, ex, ey) -> str:
    ang = math.atan2(ey - sy, ex - sx)
    h = 9
    p1 = (ex - h * math.cos(ang - 0.45), ey - h * math.sin(ang - 0.45))
    p2 = (ex - h * math.cos(ang + 0.45), ey - h * math.sin(ang + 0.45))
    return (f'<line x1="{sx:.1f}" y1="{sy:.1f}" x2="{ex:.1f}" y2="{ey:.1f}" stroke="{ACCENT}" stroke-width="2"/>'
            f'<polygon points="{ex:.1f},{ey:.1f} {p1[0]:.1f},{p1[1]:.1f} {p2[0]:.1f},{p2[1]:.1f}" fill="{ACCENT}"/>')


def _star(cx, cy, r=11) -> str:
    pts = []
    for i in range(16):
        rr = r if i % 2 == 0 else r * 0.45
        a = math.pi * i / 8
        pts.append(f"{cx + rr * math.cos(a):.1f},{cy + rr * math.sin(a):.1f}")
    return f'<polygon points="{" ".join(pts)}" fill="#ffeb3b" stroke="{ACCENT}" stroke-width="1.5"/>'


def _label(x, y, text, color, size, anchor="start") -> str:
    return (f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}" '
            f'stroke="#ffffff" stroke-width="3" paint-order="stroke">{escape(text)}</text>'
            f'<text x="{x:.1f}" y="{y:.1f}" font-size="{size}" fill="{color}" text-anchor="{anchor}">'
            f'{escape(text)}</text>')


def render_scenes(spec: DiagramSpec, layouts: list[SceneLayout]) -> list[str]:
    bounds = _bounds(spec, layouts)  # 全場面で同じ範囲を使い、固定物の位置をそろえる
    return [scene_svg(spec, lay, bounds) for lay in layouts]


def combined_svg(panels: list[str]) -> str:
    """場面を横に並べ、間に矢印を入れた1枚の図（Excelの図の枠用）。"""
    gap = 40
    w = PANEL_W * len(panels) + gap * (len(panels) - 1)
    parts = [f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{PANEL_H}" viewBox="0 0 {w} {PANEL_H}">',
             f'<rect x="0" y="0" width="{w}" height="{PANEL_H}" fill="#ffffff"/>']
    for i, svg in enumerate(panels):
        x = i * (PANEL_W + gap)
        inner = svg.split(">", 1)[1].rsplit("</svg>", 1)[0]
        parts.append(f'<g transform="translate({x},0)">{inner}</g>')
        parts.append(f'<rect x="{x + 0.5}" y="0.5" width="{PANEL_W - 1}" height="{PANEL_H - 1}" '
                     f'fill="none" stroke="#999999"/>')
        if i < len(panels) - 1:
            ax = x + PANEL_W + 8
            parts.append(_arrow(ax, PANEL_H / 2, ax + gap - 16, PANEL_H / 2).replace(ACCENT, "#555555"))
    parts.append("</svg>")
    return "\n".join(parts)


def svg_to_png(svg: str, path: Path, scale: float = 2.0) -> None:
    # Windows では offscreen だと日本語フォントが読めないため、通常のプラットフォームを使う
    if os.name != "nt":
        os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtCore import QByteArray, QRectF
    from PySide6.QtGui import QGuiApplication, QImage, QPainter
    from PySide6.QtSvg import QSvgRenderer

    _app = QGuiApplication.instance() or QGuiApplication([])
    renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
    size = renderer.defaultSize()
    img = QImage(int(size.width() * scale), int(size.height() * scale), QImage.Format.Format_ARGB32)
    img.fill(0xFFFFFFFF)
    painter = QPainter(img)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    renderer.render(painter, QRectF(0, 0, img.width(), img.height()))
    painter.end()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not img.save(str(path)):
        raise OSError(f"PNGを保存できません: {path}")
