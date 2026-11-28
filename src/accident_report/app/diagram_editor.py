"""図の手直し画面: 物をドラッグで動かし、ボタンで回す・図から外す。

手直しは場面ごとに DiagramState.manual / hidden に保存し、自動レイアウトの上に重ねて描く。
画面上の 1m = SCALE ピクセル。角度は 0 = 上、時計回り（図の描き方と同じ）。
"""

from __future__ import annotations

from PySide6.QtCore import QByteArray, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QPainter, QPen
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtSvgWidgets import QGraphicsSvgItem
from PySide6.QtWidgets import (
    QDialog,
    QGraphicsItem,
    QGraphicsScene,
    QGraphicsSimpleTextItem,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTabWidget,
    QVBoxLayout,
)

from accident_report.diagram import service
from accident_report.diagram.layout import size
from accident_report.diagram.parts import PARTS

SCALE = 40.0  # 1m あたりのピクセル
STEP = 15  # 回転ボタン1回の角度


class _PartItem(QGraphicsSvgItem):
    """部品の絵（中心が原点、前が上）。ドラッグで動かせる。"""

    def __init__(self, eid: str, kind: str, label: str, length_m: float, width_m: float, hidden: bool):
        self.eid = eid
        L, W = max(length_m * SCALE, 8), max(width_m * SCALE, 8)
        part = PARTS.get(kind)
        body = part.draw(L, W) if part else f'<rect x="{-W / 2}" y="{-L / 2}" width="{W}" height="{L}" fill="#fff" stroke="#222"/>'
        pad = 4
        svg = (f'<svg xmlns="http://www.w3.org/2000/svg" width="{W + pad * 2}" height="{L + pad * 2}" '
               f'viewBox="{-W / 2 - pad} {-L / 2 - pad} {W + pad * 2} {L + pad * 2}">{body}</svg>')
        super().__init__()
        self._renderer = QSvgRenderer(QByteArray(svg.encode("utf-8")))
        self.setSharedRenderer(self._renderer)
        rect = self.boundingRect()
        self.setTransformOriginPoint(rect.center())
        self.setFlags(QGraphicsItem.GraphicsItemFlag.ItemIsMovable | QGraphicsItem.GraphicsItemFlag.ItemIsSelectable)
        self.label = QGraphicsSimpleTextItem(label, self)
        self.label.setFont(QFont("Yu Gothic UI", 9))
        self.label.setPos(rect.right() + 2, rect.center().y() - 8)
        self.label.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIgnoresTransformations)
        self.set_hidden(hidden)

    def center_m(self) -> tuple[float, float]:
        c = self.mapToScene(self.boundingRect().center())
        return c.x() / SCALE, c.y() / SCALE

    def place(self, x_m: float, y_m: float, heading: float) -> None:
        rect = self.boundingRect()
        self.setRotation(heading)
        self.setPos(x_m * SCALE - rect.center().x(), y_m * SCALE - rect.center().y())

    def set_hidden(self, hidden: bool) -> None:
        self.hidden = hidden
        self.setOpacity(0.25 if hidden else 1.0)


class DiagramEditor(QDialog):
    def __init__(self, state: service.DiagramState, parent=None):
        super().__init__(parent)
        self.state = state
        self.setWindowTitle("図を手で直す")
        self.resize(1000, 720)
        lay = QVBoxLayout(self)
        help_ = QLabel("物をドラッグして動かします。物を選んでから、下のボタンで回したり、図から外したりできます。"
                       "点線の枠の中が「位置が分からなかった物」です。")
        help_.setWordWrap(True)
        lay.addWidget(help_)
        self.tabs = QTabWidget()
        lay.addWidget(self.tabs, 1)

        self.items: list[dict[str, _PartItem]] = []
        lays = service.final_layouts(state)
        auto = service.layout(state.spec)
        for lay_i, (fl, au) in enumerate(zip(lays, auto)):
            scene = QGraphicsScene(self)  # 親を付けないと部品ごと消える
            view = QGraphicsView(scene)
            view.setRenderHint(QPainter.RenderHint.Antialiasing)
            view.setDragMode(QGraphicsView.DragMode.RubberBandDrag)
            items: dict[str, _PartItem] = {}
            hidden = set(state.hidden.get(fl.scene.scene_id, []))
            # 位置が分からなかった物は、図の右側の「置き場」に並べる
            xs = [p.x for p in au.poses.values()] or [0]
            parking_x = max(xs) + 4
            parking = [e for e in au.unplaced if e not in fl.poses]
            for eid in (list(fl.poses) + parking + [e for e in hidden if e not in fl.poses and e not in parking]):
                ent = state.spec.entity(eid)
                if ent is None or eid in items:
                    continue
                L, W = size(state.spec, eid)
                item = _PartItem(eid, ent.kind, ent.label, L, W, hidden=eid in hidden)
                scene.addItem(item)
                if eid in fl.poses:
                    p = fl.poses[eid]
                    item.place(p.x, p.y, p.heading)
                elif eid in au.poses:
                    p = au.poses[eid]
                    item.place(p.x, p.y, p.heading)
                else:
                    slot = parking.index(eid) if eid in parking else len(parking)
                    item.place(parking_x, slot * 1.5 - 3, 0)  # 点線の枠（置き場）の中に縦に並べる
                items[eid] = item
            if parking:
                r = QRectF((parking_x - 1.5) * SCALE, -4 * SCALE, 3 * SCALE, (len(parking) * 1.5 + 2) * SCALE)
                scene.addRect(r, QPen(QColor("#c62828"), 1, Qt.PenStyle.DashLine), QBrush(Qt.BrushStyle.NoBrush))
            scene.setSceneRect(scene.itemsBoundingRect().adjusted(-200, -200, 200, 200))
            self.items.append(items)
            self.tabs.addTab(view, fl.scene.phase)

        row = QHBoxLayout()
        for text, fn in (("⟲ 左に回す", lambda: self._rotate(-STEP)), ("⟳ 右に回す", lambda: self._rotate(STEP)),
                         ("図から外す／戻す", self._toggle_hidden), ("この場面の手直しを取り消す", self._reset)):
            b = QPushButton(text)
            b.clicked.connect(fn)
            row.addWidget(b)
        row.addStretch()
        cancel = QPushButton("やめる")
        cancel.clicked.connect(self.reject)
        ok = QPushButton("この位置で図を描き直す")
        ok.setObjectName("primary")
        ok.clicked.connect(self._accept)
        row.addWidget(cancel)
        row.addWidget(ok)
        lay.addLayout(row)

    def _selected(self) -> list[_PartItem]:
        items = self.items[self.tabs.currentIndex()]
        return [it for it in items.values() if it.isSelected()]

    def _rotate(self, deg: float) -> None:
        for it in self._selected():
            it.setRotation((it.rotation() + deg) % 360)

    def _toggle_hidden(self) -> None:
        for it in self._selected():
            it.set_hidden(not it.hidden)

    def _reset(self) -> None:
        idx = self.tabs.currentIndex()
        sid = self.state.spec.scenes[idx].scene_id
        self.state.manual.pop(sid, None)
        self.state.hidden.pop(sid, None)
        auto = service.layout(self.state.spec)[idx]
        for eid, it in self.items[idx].items():
            it.set_hidden(False)
            if eid in auto.poses:
                p = auto.poses[eid]
                it.place(p.x, p.y, p.heading)

    def _accept(self) -> None:
        auto = service.layout(self.state.spec)
        for idx, items in enumerate(self.items):
            sid = self.state.spec.scenes[idx].scene_id
            manual: dict[str, service.ManualPose] = {}
            hidden: list[str] = []
            for eid, it in items.items():
                if it.hidden:
                    hidden.append(eid)
                    continue
                x, y = it.center_m()
                h = it.rotation() % 360
                p = auto[idx].poses.get(eid)
                # 自動の位置から動かしていない物は記録しない（後の質問の回答で自動の位置が変わるため）
                if p is None or abs(p.x - x) > 0.05 or abs(p.y - y) > 0.05 or abs((p.heading - h + 180) % 360 - 180) > 1:
                    manual[eid] = service.ManualPose(x=round(x, 2), y=round(y, 2), heading=round(h, 1))
            self.state.manual[sid] = manual
            self.state.hidden[sid] = hidden
        self.accept()
