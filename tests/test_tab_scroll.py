from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import QTabWidget

from klotse.ui.tab_scroll import ScrollableTabBar


def wheel(bar, delta):
    event = QWheelEvent(QPointF(5, 5), QPointF(bar.mapToGlobal(QPoint(5, 5))), QPoint(), QPoint(0, delta),
                        Qt.NoButton, Qt.NoModifier, Qt.ScrollPhase.NoScrollPhase, False)
    bar.wheelEvent(event)
    return event


def test_mouse_wheel_switches_through_cluster_or_resource_tabs(qtbot):
    tabs = QTabWidget()
    tabs.setTabBar(ScrollableTabBar())
    for name in ("Overview", "Nodes", "Pods", "Workloads"):
        tabs.addTab(QTabWidget(), name)
    qtbot.addWidget(tabs)
    tabs.setCurrentIndex(1)

    assert wheel(tabs.tabBar(), -120).isAccepted()
    assert tabs.currentIndex() == 2
    assert wheel(tabs.tabBar(), 120).isAccepted()
    assert tabs.currentIndex() == 1


def test_wheel_at_the_end_does_not_wrap(qtbot):
    tabs = QTabWidget()
    tabs.setTabBar(ScrollableTabBar())
    tabs.addTab(QTabWidget(), "Overview")
    tabs.addTab(QTabWidget(), "Nodes")
    qtbot.addWidget(tabs)
    tabs.setCurrentIndex(0)
    event = wheel(tabs.tabBar(), 120)
    assert not event.isAccepted()
    assert tabs.currentIndex() == 0
