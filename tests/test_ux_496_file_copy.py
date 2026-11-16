"""#496: a copy's local path is checked while the dialog is still open, not after it has closed."""
import pytest

from klotse.ui import file_copy
from klotse.ui.file_copy import DOWNLOAD, UPLOAD, check_local


def test_an_upload_source_that_does_not_exist_is_refused(tmp_path):
    with pytest.raises(ValueError, match="does not exist"):
        check_local(UPLOAD, str(tmp_path / "missing.txt"))


def test_a_download_into_a_missing_folder_is_refused(tmp_path):
    with pytest.raises(ValueError, match="folder"):
        check_local(DOWNLOAD, str(tmp_path / "no-such-dir" / "out.txt"))


def test_a_good_local_path_is_returned_absolute(tmp_path):
    source = tmp_path / "a.txt"
    source.write_text("x")
    assert check_local(UPLOAD, str(source)) == str(source)
    assert check_local(DOWNLOAD, str(tmp_path / "new.txt")) == str(tmp_path / "new.txt")


def test_the_dialog_refuses_a_missing_upload_source_before_it_closes(qtbot, tmp_path):
    dialog = file_copy.FileCopyDialog.__new__(file_copy.FileCopyDialog)  # the values() path only, no window needed
    dialog.remote = type("T", (), {"text": lambda self: "/tmp/x"})()
    dialog.local = type("T", (), {"text": lambda self: str(tmp_path / "gone.txt")})()
    dialog.container = type("C", (), {"currentText": lambda self: "app"})()
    dialog.direction = lambda: UPLOAD
    with pytest.raises(ValueError, match="does not exist"):
        dialog.values()
