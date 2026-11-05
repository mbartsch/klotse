"""An access key (with an optional session token) typed into the AWS Plugin's settings."""
from PySide6.QtWidgets import QLabel, QLineEdit

from klotse.plugins.manager import Loaded
from klotse.ui.plugins_panel import PluginsPanel
from klotse_plugins.aws import AwsPlugin
from klotse_plugins.aws.awscreds import AwsCredentials, CredentialSource
from klotse_plugins.aws.console import keys_from, profile_for
from test_plugin_settings import manager_for

KEY = {"access_key_id": "AKIAEXAMPLE", "secret_access_key": "s3cret-value", "session_token": "tok-123"}


def test_both_the_key_and_the_secret_are_needed_and_the_token_is_optional():
    assert keys_from({}) is None
    assert keys_from({"access_key_id": "AKIAEXAMPLE"}) is None
    assert keys_from({"secret_access_key": "x"}) is None
    keys = keys_from({"access_key_id": " AKIAEXAMPLE ", "secret_access_key": "x"})
    assert keys == AwsCredentials("AKIAEXAMPLE", "x", "")
    assert keys_from(KEY).token == "tok-123"


def test_typed_keys_replace_every_profile():
    settings = {"aws_profile": "default-p", "profile_map": "ctx = mapped-p", "cluster_profile": "mine", **KEY}
    assert isinstance(profile_for("ctx", settings), AwsCredentials)
    settings.pop("access_key_id")
    assert profile_for("ctx", settings) == "mine"  # an incomplete key is ignored


def test_what_messages_show_never_contains_the_secret_or_the_token():
    keys = keys_from(KEY)
    shown = f"{keys} {keys!r} {[keys]}"
    assert "AKIAEXAMPLE" in shown and "s3cret-value" not in shown and "tok-123" not in shown


def test_the_credential_source_uses_the_keys_and_runs_nothing():
    def boom(*a, **k):
        raise AssertionError("the AWS CLI must not be run")

    source = CredentialSource(keys_from(KEY), run=boom, which=lambda name: "aws", environ={"AWS_ACCESS_KEY_ID": "other"})
    got = source.get()
    assert (got.access_key, got.secret_key, got.token) == ("AKIAEXAMPLE", "s3cret-value", "tok-123")


def test_boto3_gets_the_keys_and_the_token(monkeypatch):
    import sys
    import types

    seen = {}
    fake = types.ModuleType("boto3")
    fake.Session = lambda **kw: seen.update(kw) or object()
    monkeypatch.setitem(sys.modules, "boto3", fake)
    from klotse_plugins.aws.ec2 import boto3_session

    boto3_session(keys_from(KEY))
    assert seen == {"aws_access_key_id": "AKIAEXAMPLE", "aws_secret_access_key": "s3cret-value", "aws_session_token": "tok-123"}
    seen.clear()
    boto3_session(keys_from({**KEY, "session_token": ""}))
    assert seen["aws_session_token"] is None


def test_the_settings_page_hides_the_secret_and_shows_a_big_warning(qtbot, tmp_path):
    manager = manager_for(tmp_path)
    manager.loaded = [Loaded("aws", "bundled", "aws", AwsPlugin())]
    manager.set_enabled("aws", True)
    panel = PluginsPanel(manager)
    qtbot.addWidget(panel)
    panel.show()
    panel.list.setCurrentRow(0)
    hidden = [e for e in panel.findChildren(QLineEdit) if e.echoMode() == QLineEdit.Password]
    assert len(hidden) == 2  # the secret access key and the session token
    notes = [label for label in panel.findChildren(QLabel) if "accept no responsibility" in label.text()]
    assert len(notes) == 1 and "plain text" in notes[0].text() and "read-only" in notes[0].text()
