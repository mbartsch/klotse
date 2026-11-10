from klotse.ui.dialog_sizing import bounded_size, resize_dialog


def test_bounded_size_preserves_preferred_size_with_room_to_grow():
    assert bounded_size(800, 600, 1920, 1080) == (800, 600)


def test_bounded_size_keeps_initial_dialog_inside_available_area():
    assert bounded_size(1200, 900, 900, 700) == (868, 668)
    assert bounded_size(600, 400, 20, 18) == (1, 1)


def test_resize_dialog_uses_available_screen_geometry():
    class Rect:
        def width(self):
            return 900

        def height(self):
            return 700

    class Screen:
        def availableGeometry(self):
            return Rect()

    class Dialog:
        def screen(self):
            return Screen()

        def sizeHint(self):
            return Rect()

        def resize(self, width, height):
            self.size = (width, height)

    dialog = Dialog()
    assert resize_dialog(dialog, 1200, 900) == (868, 668)
    assert dialog.size == (868, 668)
