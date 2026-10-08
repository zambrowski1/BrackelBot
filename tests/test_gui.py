# SPDX-License-Identifier: MIT
import os
import pytest
pytest.importorskip('PySide6')
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QEventLoop, QTimer, QCoreApplication, QEvent
from wiki_stats.gui import MainWindow
from wiki_stats.audit_logger import AuditLogger
from wiki_stats.models import Mode
from stage23_helpers import MemoryClient,package as new_package,stats_operation


def test_gui_worker_diff_decision_report(tmp_path, package, client):
    app = QApplication.instance() or QApplication([])
    window = MainWindow(lambda: client, AuditLogger(tmp_path/'audit.jsonl'))
    window.package = package
    window.check()
    loop = QEventLoop()
    window.worker.finished.connect(loop.quit)
    QTimer.singleShot(10000, loop.quit)
    loop.exec()
    app.processEvents()
    assert not window.worker.isRunning()
    assert len(window.rows) == 1
    assert not window.publish_button.isEnabled()
    window.table.selectRow(0)
    window.show_diff()
    assert '@@' in window.diff.toPlainText()
    window.decide('approved')
    assert window.rows[0][1].decision == 'approved'
    window.decide('rejected')
    assert window.rows[0][1].decision == 'rejected'
    window.close()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    app.processEvents()


def check_window(window,app):
    window.check()
    loop=QEventLoop();window.worker.finished.connect(loop.quit);QTimer.singleShot(10000,loop.quit);loop.exec();app.processEvents()
    assert not window.worker.isRunning()


def test_error_decision_and_confirmation_disabled(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(MemoryClient,AuditLogger(tmp_path/'audit.jsonl'))
    op=stats_operation();op['expected']['goals']=99
    window.package=new_package([op]);check_window(window,app)
    window.table.selectRow(0);window.show_diff()
    assert window.table.item(0,7).text()=='Ошибка проверки'
    assert not window.approve_button.isEnabled() and not window.publish_button.isEnabled()
    window.close();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()


def test_manual_mode_confirmations_invalidated_on_mode_change(tmp_path):
    app=QApplication.instance() or QApplication([])
    window=MainWindow(MemoryClient,AuditLogger(tmp_path/'audit.jsonl'))
    window.mode_box.setCurrentIndex(1)
    window.package=new_package([stats_operation()]);check_window(window,app)
    window.table.selectRow(0)
    assert not window.approve_button.isEnabled()
    window.show_diff();assert window.approve_button.isEnabled()
    window.decide('approved');assert window.publish_button.isEnabled()
    window.mode_box.setCurrentIndex(0)
    assert window.rows[0][1].decision=='pending' and not window.publish_button.isEnabled()
    assert not window.approve_button.isEnabled()
    window.close();QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete);app.processEvents()
