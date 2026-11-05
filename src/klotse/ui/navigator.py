from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QBrush, QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QDialog, QInputDialog, QMenu, QMessageBox, QTreeWidget, QTreeWidgetItem

from .cluster_groups import clean_path, inside, leaf_name, parent_path
from .group_suggest import SuggestGroupsDialog
from . import theme

ACTIVE_STATES = ("connecting", "connected")  # shown in the Connected section
STATE_COLORS = {"idle": "#6b7280", "connecting": "#f5a524", "connected": "#2fbf71", "failed": "#e5484d"}

# Shown under the connected cluster, Lens order. A leaf is (key, label); a group is
# (label, [leaves], expanded_by_default).
VIEWS = [
    ("overview", "Overview"),
    ("nodes", "Nodes"),
    ("Workloads", [("workloads", "Overview"), ("pods", "Pods"), ("deployments", "Deployments"),
                   ("daemonsets", "DaemonSets"), ("statefulsets", "StatefulSets"),
                   ("replicasets", "ReplicaSets"), ("jobs", "Jobs"), ("cronjobs", "CronJobs")], True),
    ("Config", [("configmaps", "ConfigMaps"), ("secrets", "Secrets")], False),
    ("Network", [("services", "Services"), ("ingresses", "Ingresses"),
                 ("networkpolicies", "Network Policies")], False),
    ("Storage", [("persistentvolumeclaims", "Persistent Volume Claims"),
                 ("persistentvolumes", "Persistent Volumes"), ("storageclasses", "Storage Classes")], False),
    ("Access Control", [("serviceaccounts", "Service Accounts"), ("roles", "Roles"),
                        ("rolebindings", "Role Bindings"), ("clusterroles", "Cluster Roles"),
                        ("clusterrolebindings", "Cluster Role Bindings")], False),
    ("namespaces", "Namespaces"),
    ("events", "Events"),
    ("Custom Resources", [("crds", "Definitions")], False),
]


def dot_icon(color):
    pm = QPixmap(12, 12)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    p.setBrush(QBrush(QColor(color)))
    p.setPen(Qt.NoPen)
    p.drawEllipse(1, 1, 10, 10)
    p.end()
    return QIcon(pm)


class Navigator(QTreeWidget):
    """Lens-style sidebar. Clusters are split into Connected / Disconnected sections, each with a status dot;
    a connected cluster expands into its resource views. Right-click a cluster to connect or disconnect it.

    The Disconnected section can be organised in groups (nested folders, see cluster_groups.ClusterGroups): right-click
    it or a cluster to create groups, move clusters or get groups suggested from the context names. A connected cluster
    is listed flat under Connected and returns to its group when disconnected."""

    context_clicked = Signal(str)
    view_requested = Signal(str, str)  # context name, view key
    connect_requested = Signal(str)
    disconnect_requested = Signal(str)
    reconnect_requested = Signal(str)
    plugin_settings_requested = Signal(str, str)  # plugin id, context name: a plugin's settings for that cluster
    groups_changed = Signal()  # the groups were edited; whoever owns the ClusterGroups saves them

    ROLE_KIND = Qt.UserRole
    ROLE_KEY = Qt.UserRole + 1

    def __init__(self):
        super().__init__()
        self.setHeaderHidden(True)
        self.setIndentation(14)
        self._clusters = {}
        self._order = {}  # name -> position in the kubeconfig, so each section keeps a stable order
        self._states = {}
        self.groups = None  # a ClusterGroups, or None for a plain list
        self.plugin_entries = lambda: []  # () -> [(plugin id, menu text)] offered on connected clusters (set by the window)
        self._group_items = {}  # group path -> its folder item
        self._connected_hdr = self._idle_hdr = None
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self._show_menu)
        self.itemClicked.connect(self._on_click)

    def set_contexts(self, names, groups=None):
        self.clear()
        self._clusters, self._states, self._group_items = {}, {}, {}
        self.groups = groups
        self._order = {n: i for i, n in enumerate(names)}
        self._connected_hdr = self._header("Connected")
        self._idle_hdr = self._header("Disconnected")
        for n in names:
            it = QTreeWidgetItem([n])
            it.setData(0, self.ROLE_KIND, "cluster")
            it.setData(0, self.ROLE_KEY, n)
            it.setIcon(0, dot_icon(STATE_COLORS["idle"]))
            self._clusters[n] = it
            self._states[n] = "idle"
        self._layout_idle()

    def _group_of(self, name):
        return self.groups.group_of(name) if self.groups is not None else ""

    def _idle_parent(self, name):
        return self._group_items.get(self._group_of(name), self._idle_hdr)

    def _insert_cluster(self, parent, item):
        """Put a cluster under `parent` after its group folders, in kubeconfig order."""
        rank = self._order[item.data(0, self.ROLE_KEY)]
        folders = clusters_before = 0
        for i in range(parent.childCount()):
            child = parent.child(i)
            if child.data(0, self.ROLE_KIND) == "group":
                folders += 1
            elif child.data(0, self.ROLE_KIND) == "cluster" and self._order[child.data(0, self.ROLE_KEY)] < rank:
                clusters_before += 1
        parent.insertChild(folders + clusters_before, item)

    def _layout_idle(self):
        """(Re)build the Disconnected section: group folders first, then the clusters without a group. Clusters
        that are connected are not touched."""
        expanded = {path: item.isExpanded() for path, item in self._group_items.items()}
        idle = [name for name in self._order if self._states.get(name) not in ACTIVE_STATES]
        for name in idle:
            item = self._clusters[name]
            if item.parent() is not None:
                item.parent().removeChild(item)
        while self._idle_hdr.childCount():
            self._idle_hdr.takeChild(0)  # what is left are the old group folders
        self._group_items = {}
        for path in self.groups.groups() if self.groups is not None else []:
            folder = QTreeWidgetItem([leaf_name(path)])
            folder.setData(0, self.ROLE_KIND, "group")
            folder.setData(0, self.ROLE_KEY, path)
            self._group_items.get(parent_path(path), self._idle_hdr).addChild(folder)
            folder.setExpanded(expanded.get(path, True))
            self._group_items[path] = folder
        for name in idle:
            self._insert_cluster(self._idle_parent(name), self._clusters[name])
        self._update_headers()

    def _header(self, label):
        h = QTreeWidgetItem([label])
        h.setData(0, self.ROLE_KIND, "header")
        h.setFlags(Qt.ItemIsEnabled)  # not selectable
        f = h.font(0)
        f.setBold(True)
        h.setFont(0, f)
        h.setForeground(0, QBrush(QColor(theme.MUTED)))
        self.addTopLevelItem(h)
        h.setExpanded(True)
        return h

    def _update_headers(self):
        idle = [name for name, state in self._states.items() if state not in ACTIVE_STATES]
        self._connected_hdr.setText(0, f"CONNECTED ({self._connected_hdr.childCount()})")
        self._idle_hdr.setText(0, f"DISCONNECTED ({len(idle)})")
        for path, folder in self._group_items.items():
            folder.setText(0, f"{leaf_name(path)} ({sum(1 for name in idle if inside(self._group_of(name), path))})")
        for name, item in self._clusters.items():
            group = self._group_of(name)
            item.setToolTip(0, f"Group: {group}" if group else "")

    def cluster_item(self, name):
        return self._clusters.get(name)

    def state(self, name):
        return self._states.get(name)

    def set_state(self, name, state):
        item = self._clusters.get(name)
        if item is None:
            return
        self._states[name] = state
        item.setIcon(0, dot_icon(STATE_COLORS[state]))
        want = self._connected_hdr if state in ACTIVE_STATES else self._idle_parent(name)
        if item.parent() is want:
            self._update_headers()
            return
        was_current = self.currentItem() is item
        if item.parent() is not None:
            item.parent().removeChild(item)
        self._insert_cluster(want, item)
        self._update_headers()
        if was_current:
            self.setCurrentItem(item)

    def show_views(self, name):
        """Expand `name` into its resource views. Any number of clusters can be expanded at once."""
        item = self._clusters.get(name)
        if item is None or item.childCount():
            return
        for entry in VIEWS:
            if len(entry) == 3:
                label, leaves, expanded = entry
                group = QTreeWidgetItem([label])
                item.addChild(group)
                for k, lab in leaves:
                    self._leaf(group, k, lab)
                group.setExpanded(expanded)
            else:
                self._leaf(item, *entry)
        item.setExpanded(True)

    def _leaf(self, parent, key, label):
        it = QTreeWidgetItem([label])
        it.setData(0, self.ROLE_KIND, "view")
        it.setData(0, self.ROLE_KEY, key)
        parent.addChild(it)

    def hide_views(self, name):
        item = self._clusters.get(name)
        if item is not None:
            item.takeChildren()

    def select_cluster(self, name):
        item = self._clusters.get(name)
        if item is not None:
            self.setCurrentItem(item)

    def select_view(self, name, key):
        def walk(item):
            for i in range(item.childCount()):
                c = item.child(i)
                if c.data(0, self.ROLE_KEY) == key:
                    self.setCurrentItem(c)
                    return True
                if walk(c):
                    return True
            return False

        item = self._clusters.get(name)
        if item:
            walk(item)

    @staticmethod
    def _cluster_of(item):
        """The cluster item that `item` is (or lives under); None for section headers."""
        while item is not None and item.data(0, Navigator.ROLE_KIND) != "cluster":
            item = item.parent()
        return item

    def actions_for(self, name):
        """Context-menu entries for a cluster as (label, signal) pairs."""
        if self._states.get(name) in ACTIVE_STATES:
            return [("Reconnect", self.reconnect_requested), ("Disconnect", self.disconnect_requested)]
        return [("Connect", self.connect_requested)]

    def menu_for(self, item):
        """The context menu for a sidebar item, or None when there is nothing to offer."""
        kind = item.data(0, self.ROLE_KIND) if item is not None else None
        menu = QMenu(self)
        if kind == "group":
            path = item.data(0, self.ROLE_KEY)
            menu.addAction("Connect all", lambda: self.connect_all(path))
            menu.addAction("New subgroup…", lambda: self.create_group(path))
            menu.addAction("Rename…", lambda: self.rename_group(path))
            menu.addAction("Delete group", lambda: self.delete_group(path))
        elif item is not None and item is self._idle_hdr and self.groups is not None:
            menu.addAction("New group…", lambda: self.create_group(""))
            menu.addAction("Suggest groups…", self.suggest_groups)
        else:
            cluster = self._cluster_of(item)
            if cluster is None:
                return None
            name = cluster.data(0, self.ROLE_KEY)
            for label, signal in self.actions_for(name):
                menu.addAction(label, lambda sig=signal: sig.emit(name))
            entries = self.plugin_entries() if self._states.get(name) in ACTIVE_STATES else []
            if entries:
                menu.addSeparator()
                for plugin_id, text in entries:
                    menu.addAction(text, lambda pid=plugin_id: self.plugin_settings_requested.emit(pid, name))
            if self.groups is not None:
                menu.addSeparator()
                move = menu.addMenu("Move to group")
                for path in self.groups.groups():
                    move.addAction(path, lambda p=path: self.move_cluster(name, p))
                if self.groups.groups():
                    move.addSeparator()
                move.addAction("New group…", lambda: self.create_group("", move=name))
                if self._group_of(name):
                    move.addAction("No group", lambda: self.move_cluster(name, ""))
        return menu

    def _show_menu(self, pos):
        menu = self.menu_for(self.itemAt(pos))
        if menu is not None:
            menu.exec(self.viewport().mapToGlobal(pos))

    # ---- groups --------------------------------------------------------------
    def _edited(self):
        self._layout_idle()
        self.groups_changed.emit()

    def _ask_name(self, title, text=""):
        value, accepted = QInputDialog.getText(self, title, "Group name (use / for levels, e.g. prod/eu):", text=text)
        return clean_path(value) if accepted else ""

    def create_group(self, parent="", name=None, move=None):
        """Add a group (under `parent`), optionally moving a cluster into it; returns its path or ""."""
        name = clean_path(name) if name is not None else self._ask_name("New group")
        if not name:
            return ""
        path = clean_path(f"{parent}/{name}") if parent else name
        self.groups.add_group(path)
        if move:
            self.groups.assign(move, path)
        self._edited()
        return path

    def rename_group(self, path, name=None):
        name = clean_path(name) if name is not None else self._ask_name("Rename group", leaf_name(path))
        if not name:
            return path
        new = self.groups.rename_group(path, clean_path(f"{parent_path(path)}/{name}"))
        self._edited()
        return new

    def delete_group(self, path, confirm=True):
        if confirm and QMessageBox.question(
                self, "Delete group", f"Delete the group {path}?\n\nIts clusters are kept and move up one level.",
                QMessageBox.Yes | QMessageBox.No, QMessageBox.No) != QMessageBox.Yes:
            return False
        self.groups.delete_group(path)
        self._edited()
        return True

    def move_cluster(self, name, path):
        self.groups.assign(name, path)
        self._edited()

    def connect_all(self, path):
        """Ask to connect every disconnected cluster in a group and its subgroups; returns their names."""
        names = [n for n in self.groups.contexts_in(path, list(self._order)) if self._states.get(n) not in ACTIVE_STATES]
        for name in names:
            self.connect_requested.emit(name)
        return names

    def suggest_groups(self):
        dialog = SuggestGroupsDialog(list(self._order), self.groups, self)
        accepted = dialog.exec() == QDialog.Accepted
        chosen = dialog.assignments() if accepted else {}
        dialog.deleteLater()
        for name, path in chosen.items():
            self.groups.assign(name, path)
        if chosen:
            self._edited()
        return chosen

    def _on_click(self, item, _col):
        kind = item.data(0, self.ROLE_KIND)
        if kind == "cluster":
            self.context_clicked.emit(item.data(0, self.ROLE_KEY))
        elif kind == "view":
            cluster = self._cluster_of(item)  # views live under groups under the cluster item
            self.view_requested.emit(cluster.data(0, self.ROLE_KEY), item.data(0, self.ROLE_KEY))
