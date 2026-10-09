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
from wiki_stats.manual_dialog import ManualStatsDialog
from wiki_stats.gui import TaskWorker


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


def test_manual_form_and_offline_recheck(tmp_path, snapshot):
    app = QApplication.instance() or QApplication([])
    class NoNetwork:
        def fetch_page(self, title):
            raise AssertionError('Offline form must not request Wikipedia')
    window = MainWindow(NoNetwork, AuditLogger(tmp_path/'manual-audit.jsonl'))
    dialog = ManualStatsDialog(window.client, TaskWorker, window)
    dialog.offline = True
    dialog.loaded(snapshot)
    dialog.set_busy(False)
    index = next(i for i in range(dialog.rows.count()) if dialog.rows.itemData(i).structure == 'club_table'
                 and dialog.rows.itemData(i).period == '2026/27' and dialog.rows.itemData(i).category == 'Чемпионат')
    dialog.rows.setCurrentIndex(index)
    assert dialog.games.value() == 3 and dialog.goals.value() == 0
    dialog.games.setValue(4); dialog.goals.setValue(1)
    dialog.prepare()
    assert dialog.package is None and 'источник' in dialog.status.text()
    dialog.source.setText('https://example.org/stats'); dialog.coverage.setText('2026-10-05')
    dialog.scope.setChecked(True)
    dialog.prepare()
    window.apply_manual_entry(dialog.package, snapshot, offline=True)
    assert window.rows[0][1].status == 'ready'
    assert not window.plans[0].publishable and not window.publish_button.isEnabled()
    assert window.save_package_button.isEnabled()
    window.check()
    assert not window.plans[0].publishable and window.worker is None
    dialog.clear_article()
    assert dialog.snapshot is None and dialog.rows.count() == 0
    assert not dialog.scope.isChecked() and not dialog.source.text()
    assert not dialog.buttons.button(dialog.buttons.StandardButton.Ok).isEnabled()
    window.close(); QCoreApplication.sendPostedEvents(None,QEvent.Type.DeferredDelete); app.processEvents()


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
