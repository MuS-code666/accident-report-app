"""画面の見た目（文字入力を減らし、大きなボタンで操作できるようにする）。"""

STYLE = """
QWidget { font-family: "Yu Gothic UI", "Meiryo UI"; font-size: 15px; }
QLabel#title { font-size: 26px; font-weight: bold; }
QLabel#question { font-size: 24px; font-weight: bold; padding: 8px 0; }
QLabel#chip { color: #1f4e79; font-weight: bold; }
QLabel#muted { color: #555; }
QLabel#warn { color: #b00020; font-weight: bold; }
QPushButton { min-height: 44px; padding: 4px 16px; border: 1px solid #999; border-radius: 6px; background: #f4f4f4; color: #1b1f24; }
QPushButton:hover { background: #e8e8e8; }
QPushButton:disabled { color: #aaa; background: #f8f8f8; border-color: #ddd; }
QPushButton#primary { background: #1f6feb; color: white; border: none; font-weight: bold; font-size: 17px; min-height: 56px; }
QPushButton#primary:hover { background: #1a5fcc; }
QPushButton#primary:disabled { background: #9db9e8; }
QPushButton#record { background: #2da44e; color: white; border: none; font-size: 20px; font-weight: bold; min-height: 72px; }
QPushButton#record[recording="true"] { background: #cf222e; }
QPushButton#record:disabled { background: #a8d5b5; }
QPlainTextEdit#answer { font-size: 18px; }
QListWidget { font-size: 14px; }
"""


def setup_app(app) -> None:
    """Windows のダークモードに関係なく、明るい配色で表示する（白黒印刷の帳票と見た目をそろえる）。"""
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QColor, QPalette

    try:
        app.styleHints().setColorScheme(Qt.ColorScheme.Light)
    except AttributeError:
        pass
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.ColorRole.Window, "#f6f7f9"), (QPalette.ColorRole.WindowText, "#1b1f24"),
                        (QPalette.ColorRole.Base, "#ffffff"), (QPalette.ColorRole.AlternateBase, "#f0f2f5"),
                        (QPalette.ColorRole.Text, "#1b1f24"), (QPalette.ColorRole.Button, "#f4f4f4"),
                        (QPalette.ColorRole.ButtonText, "#1b1f24"), (QPalette.ColorRole.Highlight, "#1f6feb"),
                        (QPalette.ColorRole.HighlightedText, "#ffffff"), (QPalette.ColorRole.ToolTipBase, "#ffffe0"),
                        (QPalette.ColorRole.ToolTipText, "#1b1f24"), (QPalette.ColorRole.PlaceholderText, "#8a8f98")):
        pal.setColor(role, QColor(color))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.ButtonText, QColor("#a0a4aa"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.Text, QColor("#a0a4aa"))
    pal.setColor(QPalette.ColorGroup.Disabled, QPalette.ColorRole.WindowText, QColor("#a0a4aa"))
    app.setPalette(pal)
    app.setStyleSheet(STYLE)
