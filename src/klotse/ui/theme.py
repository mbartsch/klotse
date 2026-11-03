from PySide6.QtGui import QColor, QPalette

BG = "#1e2127"
BASE = "#16181d"
BORDER = "#2b2f38"
TEXT = "#d7dae0"
MUTED = "#9aa3b2"
ACCENT = "#3d7eff"

QSS = f"""
QMainWindow, QDialog {{ background: {BG}; }}
QToolBar {{ background: {BG}; border: 0; border-bottom: 1px solid {BORDER}; spacing: 6px; padding: 4px; }}
QToolBar QToolButton {{ padding: 4px 10px; border-radius: 4px; }}
QToolBar QToolButton:hover {{ background: {BORDER}; }}
QStatusBar {{ background: {BG}; color: {MUTED}; border-top: 1px solid {BORDER}; }}
QTreeView {{ background: {BASE}; border: 0; outline: 0; }}
QTreeView::item {{ padding: 4px 2px; }}
QTreeView::item:selected {{ background: {ACCENT}; color: white; }}
QTabWidget::pane {{ border: 0; border-top: 1px solid {BORDER}; }}
QTabBar::tab {{ background: {BG}; color: {MUTED}; padding: 7px 14px; border: 0; border-bottom: 2px solid transparent; }}
QTabBar::tab:selected {{ color: white; border-bottom: 2px solid {ACCENT}; }}
QTabBar::tab:hover {{ color: white; }}
QHeaderView::section {{ background: {BG}; color: {MUTED}; border: 0; border-bottom: 1px solid {BORDER};
                        padding: 4px 8px; font-weight: normal; }}
QTableView {{ background: {BASE}; gridline-color: transparent; border: 0; selection-background-color: #26406f; }}
QTableView::item {{ padding: 0 6px; }}
QDockWidget {{ titlebar-close-icon: none; }}
QDockWidget::title {{ background: {BG}; padding: 6px 8px; border-bottom: 1px solid {BORDER}; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{ background: {BASE}; border: 1px solid {BORDER}; border-radius: 4px; padding: 3px 6px; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus {{ border-color: {ACCENT}; }}
QPlainTextEdit {{ border-radius: 0; }}
QPushButton {{ background: {BORDER}; border: 0; border-radius: 4px; padding: 5px 12px; }}
QPushButton:hover {{ background: #394050; }}
QMenu {{ background: {BG}; border: 1px solid {BORDER}; padding: 4px; }}
QMenu::item {{ padding: 5px 22px; border-radius: 3px; }}
QMenu::item:selected {{ background: {ACCENT}; }}
QMenu::separator {{ height: 1px; background: {BORDER}; margin: 4px 6px; }}
"""


def apply(app):
    app.setStyle("Fusion")
    pal = QPalette()
    for role, color in ((QPalette.Window, BG), (QPalette.WindowText, TEXT), (QPalette.Base, BASE),
                        (QPalette.AlternateBase, BG), (QPalette.Text, TEXT), (QPalette.Button, BORDER),
                        (QPalette.ButtonText, TEXT), (QPalette.ToolTipBase, BG), (QPalette.ToolTipText, TEXT),
                        (QPalette.Highlight, ACCENT), (QPalette.HighlightedText, "#ffffff"),
                        (QPalette.PlaceholderText, MUTED)):
        pal.setColor(role, QColor(color))
    app.setPalette(pal)
    app.setStyleSheet(QSS)
