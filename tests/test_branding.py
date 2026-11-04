import pytest
from PySide6.QtGui import QColor, QImage

from klotse.ui import branding


def make_image(path, width=1280, height=768, fmt="JPG"):
    image = QImage(width, height, QImage.Format_RGB32)
    image.fill(QColor("#224466"))
    assert image.save(str(path), fmt)


@pytest.fixture
def assets(tmp_path, monkeypatch):
    monkeypatch.setattr(branding, "ASSETS", tmp_path)
    return tmp_path


def test_without_a_logo_nothing_is_shown_and_nothing_fails(qtbot, assets):
    assert branding.logo_path() is None
    assert branding.app_icon().isNull()
    assert branding.square_logo().isNull()


def test_logo_is_cropped_to_a_centered_square(qtbot, assets):
    image = QImage(1408, 768, QImage.Format_RGB32)
    image.fill(QColor("white"))
    for x in range(320, 1088):  # the artwork occupies the centered 768x768 square
        for y in range(768):
            image.setPixelColor(x, y, QColor("#2255bb"))
    assert image.save(str(assets / "klotse_logo.jpg"), "JPG")
    assert branding.logo_path() == assets / "klotse_logo.jpg"
    assert not branding.app_icon().isNull()
    square = branding.square_logo()
    assert (square.width(), square.height()) == (768, 768)
    corner = square.toImage().pixelColor(5, 5)
    assert corner.blue() > 150 and corner.red() < 80  # no white margin
    scaled = branding.square_logo(128)
    assert (scaled.width(), scaled.height()) == (128, 128)


def test_small_logo_is_not_enlarged(qtbot, assets):
    make_image(assets / "logo.jpg", 300, 200)
    small = branding.square_logo(512)
    assert (small.width(), small.height()) == (200, 200)


def test_png_is_preferred_and_a_corrupt_file_is_ignored(qtbot, assets):
    make_image(assets / "logo.jpg")
    make_image(assets / "klotse_logo.png", 100, 100, "PNG")
    assert branding.logo_path() == assets / "klotse_logo.png"
    (assets / "klotse_logo.png").write_bytes(b"not an image")
    assert branding.square_logo().isNull() and branding.app_icon().isNull()  # unreadable: no icon, no crash


def test_startup_sets_the_icon_and_shows_no_splash(qtbot, monkeypatch):
    import klotse.__main__ as entry

    events = []

    class Signal:
        def connect(self, fn):
            pass

    class FakeApp:
        aboutToQuit = Signal()

        def __init__(self, argv):
            pass

        def setApplicationName(self, name):
            events.append(("name", name))

        def setDesktopFileName(self, name):
            events.append(("desktop file", name))

        def setWindowIcon(self, icon):
            events.append("icon")

        def processEvents(self):
            events.append("process")

        def exec(self):
            return 0

    class FakeWindow:
        closing = Signal()

        def __init__(self, factory):
            events.append("window.init")

        def show(self):
            events.append("window.show")

        def shutdown(self):
            pass

    monkeypatch.setattr(entry, "QApplication", FakeApp)
    monkeypatch.setattr(entry, "MainWindow", FakeWindow)
    monkeypatch.setattr(entry.theme, "apply", lambda app: None)
    assert not hasattr(entry.branding, "make_splash")
    with pytest.raises(SystemExit):
        entry.main([])  # no command-line arguments, whatever pytest was started with
    assert events == [("name", "Klotse"), ("desktop file", "klotse"), "icon", "window.init", "window.show"]
