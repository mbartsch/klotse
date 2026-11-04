from datetime import datetime

from PySide6.QtCore import QObject, QUrl, Signal
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import QMessageBox, QPlainTextEdit

from ..plugins.api import ActionRequest, Copy, Message, OpenUrl, Reload, ShowText, Terminal
from ..plugins.host import resource_info
from .resource_table import ResourceTable
from .terminal import kubectl_base

MAX_NAMES = 5  # resources named in a confirmation or the activity line before "and N more"


class TextTab(QPlainTextEdit):
    """A read-only text tab for the Logs & activity dock (it has a stop() because closing a tab calls it)."""

    def __init__(self, text=""):
        super().__init__(text)
        self.setReadOnly(True)
        self.setLineWrapMode(QPlainTextEdit.NoWrap)

    def stop(self):
        pass


def describe(resources):
    names = ", ".join(r.key for r in resources[:MAX_NAMES])
    return names + (f" and {len(resources) - MAX_NAMES} more" if len(resources) > MAX_NAMES else "")


class PluginActionRunner(QObject):
    """Runs the actions plugins add to the right-click menus for one cluster workspace.

    The menu entries come from `offered()`; `run()` confirms when the action asks to, writes an activity line, runs the
    plugin on a worker thread and then carries out what it returned. Plugin errors are reported, never raised."""

    output = Signal(str)  # progress lines; may be emitted from a worker thread

    def __init__(self, workspace):
        super().__init__(workspace)
        self.ws = workspace
        self._output_tab = None
        self.output.connect(self._append_output)

    # ---- the menu ---------------------------------------------------------------------
    def offered(self, kind, rows):
        """[(plugin name, plugin id, [Action])] to show for these rows: the actions that apply to every one."""
        resources = [resource_info(row) for row in rows]
        groups = []
        for plugin, actions in self.ws.plugins.actions_for(kind, multi=len(rows) > 1):
            shown = [a for a in actions if all(self._applies(plugin, a, r) for r in resources)]
            if shown:
                groups.append((plugin.name or plugin.id, plugin.id, shown))
        return groups

    @staticmethod
    def _applies(plugin, action, resource):
        try:
            return bool(plugin.applies(action, resource))
        except Exception:  # a plugin's filter must not break the menu: just hide the entry
            return False

    # ---- running ----------------------------------------------------------------------
    def run(self, plugin_id, key, rows, kind):
        found = self.ws.plugins.get(plugin_id)
        action = next((a for a in found.plugin.actions if a.key == key), None) if found else None
        if action is None:
            self.ws._on_error(f"The plugin action {plugin_id}:{key} is not available")
            return
        plugin = found.plugin
        label = plugin.name or plugin.id
        resources = [resource_info(row) for row in rows]
        if action.dangerous or action.confirm:
            text = action.confirm or (f"{label}: {action.title}\n\nThis plugin action can change things. "
                                      f"Run it on {describe(resources)}?")
            if QMessageBox.question(self.ws, action.title, text, QMessageBox.Yes | QMessageBox.No,
                                    QMessageBox.No) != QMessageBox.Yes:
                return
        self.note(f"{label}: {action.title} on {describe(resources)}")
        service = self.ws.service

        def api_client():
            getter = getattr(service, "api_client", None)
            if getter is None:
                raise RuntimeError("not connected to a cluster")
            return getter()

        request = ActionRequest(plugin.id, action.key, kind, self.ws.context, resources,
                                self.ws.plugins.settings_for(plugin.id, self.ws.context), log=lambda line: self.output.emit(str(line)),
                                api_client=api_client, kubectl=tuple(kubectl_base(self.ws.context, self.ws.kubeconfig)),
                                cache_dir=self.ws.plugins.cache_dir(plugin.id))
        self.ws._run(lambda: plugin.run(request), lambda result: self._done(plugin, action, result),
                     on_error=lambda message: self._failed(plugin, action, message))

    def _done(self, plugin, action, result):
        results = [] if result is None else list(result) if isinstance(result, (list, tuple)) else [result]
        for item in results:
            self._carry_out(plugin, action, item)

    def _failed(self, plugin, action, message):
        label = plugin.name or plugin.id
        self.ws.plugins.record_error(plugin.id, f"{action.title}: {message}")
        self.note(f"{label}: {action.title} failed: {message}")
        QMessageBox.warning(self.ws, f"Plugin {label}", f"{action.title} failed:\n\n{message}")

    def _carry_out(self, plugin, action, item):
        ws, label = self.ws, plugin.name or plugin.id
        if isinstance(item, Message):
            ws.message.emit(f"{label}: {item.text}")
            self.note(f"{label}: {item.text}")
            if item.level == "error":
                QMessageBox.critical(ws, label, item.text)
            elif item.level == "warning":
                QMessageBox.warning(ws, label, item.text)
        elif isinstance(item, Copy):
            QGuiApplication.clipboard().setText(str(item.text))
            ws.message.emit(f"{label}: copied {len(str(item.text)):,} characters")
        elif isinstance(item, OpenUrl):
            url = QUrl(str(item.url))
            if url.scheme() in ("http", "https") and url.host():
                QDesktopServices.openUrl(url)
            else:
                self._failed(plugin, action, f"will only open http(s) addresses, not {item.url!r}")
        elif isinstance(item, ShowText):
            tab = TextTab(str(item.text))
            ws.log_tabs.setCurrentIndex(ws.log_tabs.addTab(tab, str(item.title)))
            ws.log_dock.show()
        elif isinstance(item, Terminal):
            argv = list(item.argv) if isinstance(item.argv, (list, tuple)) else []
            if argv and all(isinstance(a, str) for a in argv):
                ws._launch_terminal(argv, f"{label}: opened a terminal")
            else:
                self._failed(plugin, action, "a Terminal result needs a non-empty list of strings")
        elif isinstance(item, Reload):
            kinds = set(item.kinds)
            for key, (widget, _title) in list(ws.views.items()):
                if isinstance(widget, ResourceTable) and key in ws.loaders and (not kinds or widget.kind in kinds):
                    if ws.tabs.indexOf(widget) >= 0:  # only lists that are open
                        ws.load_view(key)
        else:
            self._failed(plugin, action, f"returned something Klotse does not understand ({type(item).__name__})")

    # ---- the activity output ----------------------------------------------------------------
    def note(self, line):
        self.output.emit(f"[{datetime.now():%H:%M:%S}] {line}")

    def _append_output(self, line):
        ws = self.ws
        try:
            present = self._output_tab is not None and ws.log_tabs.indexOf(self._output_tab) >= 0
        except RuntimeError:  # the user closed the tab and Qt deleted it
            present = False
        if not present:
            self._output_tab = TextTab()
            ws.log_tabs.addTab(self._output_tab, "Plugins")
        self._output_tab.appendPlainText(line)
        ws.log_dock.show()
