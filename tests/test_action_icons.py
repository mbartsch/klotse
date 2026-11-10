from PySide6.QtCore import QSize
from PySide6.QtWidgets import QApplication

from klotse.ui import theme
from klotse.ui.action_icons import NAMES, action_icon


def _visible_pixels(pixmap):
    image = pixmap.toImage()
    return sum(image.pixelColor(x, y).alpha() > 0
               for y in range(image.height()) for x in range(image.width()))


def test_action_icons_render_at_toolbar_sizes_in_every_builtin_theme(qtbot):
    app = QApplication.instance()
    previous = theme.current
    try:
        for palette in theme.BUILTIN:
            theme.apply(app, palette)
            for name in NAMES:
                icon = action_icon(name)
                assert not icon.isNull(), (palette.name, name)
                for size in (16, 20, 24, 32):
                    pixmap = icon.pixmap(QSize(size, size))
                    assert not pixmap.isNull(), (palette.name, name, size)
                    assert _visible_pixels(pixmap) > size, (palette.name, name, size)
                hidpi = icon.pixmap(QSize(16, 16), 2.0)
                assert hidpi.size() == QSize(32, 32) and hidpi.devicePixelRatio() == 2.0
    finally:
        theme.apply(app, previous)


def test_action_icons_follow_the_active_palette_when_repainted(qtbot):
    app = QApplication.instance()
    previous = theme.current
    icon = action_icon("search")
    try:
        colors = []
        for palette in theme.BUILTIN:
            theme.apply(app, palette)
            image = icon.pixmap(QSize(24, 24)).toImage()
            pixels = {image.pixelColor(x, y).name()
                      for y in range(image.height()) for x in range(image.width())
                      if image.pixelColor(x, y).alpha() > 0}
            colors.append(palette.text in pixels)
        assert colors == [True] * len(theme.BUILTIN)
    finally:
        theme.apply(app, previous)
