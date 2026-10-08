# SPDX-License-Identifier: MIT
import json
from pathlib import Path
from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtGui import QFontDatabase
from PySide6.QtWidgets import (QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
                              QPushButton, QLabel, QTableWidget, QTableWidgetItem,
                              QPlainTextEdit, QFileDialog, QMessageBox, QSplitter,
                              QComboBox, QDialog, QLineEdit, QFormLayout, QDialogButtonBox)
from .json_loader import load_package
from .wiki_client import WikiClient
from .change_planner import plan_package
from .audit_logger import AuditLogger, make_report, audit_report
from .models import Mode
from .transactions import approve_change, reject_change, TEST_TITLE
from .publisher import Publisher
from .backup import save_backup, plan_restore, default_backup_dir


class TaskWorker(QThread):
    completed = Signal(object)
    failed = Signal(str, str)

    def __init__(self, function, parent=None):
        super().__init__(parent)
        self.function = function

    def run(self):
        try:
            self.completed.emit(self.function())
        except Exception as exc:
            self.failed.emit(getattr(exc,'code','execution_error'),str(exc))
        finally:
            self.function = None  # drop closures containing temporary credentials


class CheckWorker(QThread):
    completed = Signal(object, object)
    failed = Signal(str)
    progress = Signal(str)

    def __init__(self, package, client, parent=None):
        super().__init__(parent)
        self.package, self.client = package, client

    def run(self):
        try:
            plans, failures = plan_package(self.package, self.client, self.progress.emit)
            self.completed.emit(plans, failures)
        except Exception as exc:
            self.failed.emit(str(exc))


class MainWindow(QMainWindow):
    def __init__(self, client_factory=WikiClient, logger=None):
        super().__init__()
        self.client_factory = client_factory
        self.client = client_factory()
        self.logger = logger or AuditLogger()
        self.package = None
        self.plans, self.failures, self.rows = [], [], []
        self.viewed = set()
        self.worker = None
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("BrackelBot — ручное тестирование, этапы 2 и 3")
        self.resize(1300, 850)
        central = QWidget()
        layout = QVBoxLayout(central)
        self.setCentralWidget(central)
        label = QLabel("Источники статистики предоставлены пользователем; ссылки клубов/сборных проверяются через Wikipedia и Wikidata.\n"
                       "Публикация разрешена только на Участник:Zambrowski/testbot. Основное пространство и Automatic заблокированы.")
        label.setWordWrap(True)
        layout.addWidget(label)
        account_bar = QHBoxLayout()
        self.mode_box = QComboBox()
        self.mode_box.addItem('Dry Run — только просмотр',Mode.DRY_RUN.value)
        self.mode_box.addItem('Manual — тестовая публикация',Mode.MANUAL.value)
        self.login_button = QPushButton('Войти через BotPasswords')
        self.logout_button = QPushButton('Выйти')
        self.auth_label = QLabel('Не авторизован')
        for widget in [self.mode_box,self.login_button,self.logout_button,self.auth_label]: account_bar.addWidget(widget)
        layout.addLayout(account_bar)
        bar = QHBoxLayout()
        self.load_button = QPushButton("Загрузить JSON")
        self.check_button = QPushButton("Проверить статьи")
        self.show_button = QPushButton("Показать изменения")
        self.approve_button = QPushButton("Подтвердить")
        self.reject_button = QPushButton("Отклонить")
        self.publish_button = QPushButton("Опубликовать утверждённое")
        self.export_button = QPushButton("Экспортировать отчёт")
        self.backup_button = QPushButton('Сохранить исходную версию')
        self.restore_button = QPushButton('Восстановить из копии')
        for button in [self.load_button, self.check_button, self.show_button, self.export_button]:
            bar.addWidget(button)
        layout.addLayout(bar)
        decisions = QHBoxLayout()
        for button in [self.approve_button, self.reject_button, self.publish_button]:
            decisions.addWidget(button)
        decisions.addStretch()
        decisions.addWidget(self.backup_button)
        decisions.addWidget(self.restore_button)
        layout.addLayout(decisions)
        self.publish_button.setEnabled(False)
        self.publish_button.setToolTip("Публикация появится только после реализации и проверки этапа 3")
        self.table = QTableWidget(0, 8)
        self.table.setHorizontalHeaderLabels(["Статья / операция", "Параметр", "Клуб / сборная", "Сезон / период / соревнование",
                                             "Старое → новое", "Источники", "Проверка", "Решение"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.itemSelectionChanged.connect(self.update_buttons)
        self.diff = QPlainTextEdit()
        self.diff.setReadOnly(True)
        self.diff.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        split = QSplitter(Qt.Orientation.Vertical)
        split.addWidget(self.table)
        split.addWidget(self.diff)
        layout.addWidget(split)
        self.article_status = QLabel('Статьи ещё не проверены')
        self.article_status.setWordWrap(True)
        layout.addWidget(self.article_status)
        self.status = QLabel("Загрузите JSON-пакет. Сетевых запросов при запуске нет.")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.load_button.clicked.connect(self.load_json)
        self.check_button.clicked.connect(self.check)
        self.show_button.clicked.connect(self.show_diff)
        self.approve_button.clicked.connect(lambda: self.decide("approved"))
        self.reject_button.clicked.connect(lambda: self.decide("rejected"))
        self.export_button.clicked.connect(self.export)
        self.publish_button.clicked.connect(self.publish)
        self.login_button.clicked.connect(self.login)
        self.logout_button.clicked.connect(self.logout)
        self.mode_box.currentIndexChanged.connect(self.mode_changed)
        self.backup_button.clicked.connect(self.backup)
        self.restore_button.clicked.connect(self.restore)
        self.update_buttons()

    def update_buttons(self):
        busy = self.worker is not None and self.worker.isRunning()
        self.load_button.setEnabled(not busy)
        self.check_button.setEnabled(bool(self.package and self.package.get('articles')) and not busy)
        for b in [self.show_button, self.reject_button, self.export_button]:
            b.setEnabled(bool(self.rows or self.failures) and not busy)
        row = self.table.currentRow()
        selected = self.rows[row][1] if 0 <= row < len(self.rows) else None
        self.approve_button.setEnabled(not busy and selected is not None and selected.status=='ready' and selected.id in self.viewed)
        self.mode_box.setEnabled(not busy)
        self.login_button.setEnabled(not busy)
        self.logout_button.setEnabled(not busy and bool(getattr(self.client,'authenticated_user',None)))
        self.restore_button.setEnabled(not busy)
        self.backup_button.setEnabled(not busy and any(p.snapshot.title==TEST_TITLE for p in self.plans))
        self.publish_button.setEnabled(not busy and self.mode_box.currentData()==Mode.MANUAL.value
                                       and bool(getattr(self.client,'authenticated_user',None))
                                       and any(p.publishable and any(c.decision=='approved' for c in p.changes) for p in self.plans))
        self.publish_button.setToolTip('Только Manual, авторизованная сессия, подтверждённые операции тестовой страницы')

    def load_json(self):
        path, _ = QFileDialog.getOpenFileName(self, "Загрузить пакет", "", "JSON (*.json)")
        if not path:
            return
        try:
            package = load_package(path)
            self.logger.log("package_loaded", package_id=package["package_id"])
            self.package = package
            self.plans, self.failures, self.rows = [], [], []
            self.viewed.clear()
            self.table.setRowCount(0)
            self.diff.clear()
            self.status.setText(f"Пакет {package['package_id']}: {len(package['articles'])} статей. JSON проверен.")
        except Exception as exc:
            QMessageBox.warning(self, "Ошибка загрузки", str(exc))
        self.update_buttons()

    def check(self):
        if not self.package or (self.worker and self.worker.isRunning()):
            return
        self.plans, self.failures, self.rows = [], [], []
        self.viewed.clear()
        self.table.setRowCount(0)
        self.diff.clear()
        self.worker = CheckWorker(self.package, self.client, self)
        self.worker.progress.connect(self.status.setText)
        self.worker.completed.connect(self.checked)
        self.worker.failed.connect(self.failed)
        self.worker.finished.connect(self.update_buttons)
        self.worker.start()
        self.update_buttons()

    def failed(self, message):
        self.status.setText(f"Проверка завершилась ошибкой: {message}")
        try:
            self.logger.log("check_failed", message=message)
        except OSError:
            pass
        QMessageBox.warning(self, "Ошибка проверки", message)

    def checked(self, plans, failures):
        self.plans, self.failures = plans, failures
        report = make_report(self.package, plans, failures)
        try:
            audit_report(self.logger, report)
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка журнала", str(exc))
        self.rows = [(p, c) for p in plans for c in p.changes]
        self.table.setRowCount(len(self.rows))
        for i, (p, c) in enumerate(self.rows):
            op = c.operation
            values = [f"{p.snapshot.title}\n{c.id}\nГруппа: {c.group_id}",op['type'],op.get('entity',{}).get('name','Резервная копия'),
                      f"{op.get('season') or op.get('target',{}).get('period',op.get('payload',{}).get('period',''))}\n{op.get('competition',{}).get('name','')}",
                      f"{json.dumps(op.get('expected'), ensure_ascii=False)} → {json.dumps(op.get('new',op.get('payload',{})), ensure_ascii=False)}",
                      "\n".join(s['url'] for s in op.get('sources',[])),f"{c.error_layer}: {c.status}\n{c.message}",self.decision_text(c)]
            for j, value in enumerate(values):
                self.table.setItem(i, j, QTableWidgetItem(value))
        for column, width in enumerate([150, 110, 120, 160, 140, 160, 210, 110]):
            self.table.setColumnWidth(column, width)
        self.table.resizeRowsToContents()
        self.status.setText(f"Проверено статей: {len(plans)}. Ошибок получения: {len(failures)}. " +
                            " | ".join(f"{f['title']}: {f['status']} — {f['message']}" for f in failures))
        self.diff.setPlainText("\n\n".join(p.diff+"\n"+"\n".join(p.warnings) for p in plans))
        self.refresh_status()

    def show_diff(self):
        row = self.table.currentRow()
        if 0 <= row < len(self.rows):
            p, c = self.rows[row]
            self.diff.setPlainText(f"Ревизия: {p.snapshot.revid}\n{c.status}: {c.message}\n"
                                   f"Источники: {c.source_verification}\nДата заявленного охвата: {c.operation.get('as_of','резервная копия')}\n\n"
                                   + (c.diff or "Изменений нет.")+"\n"+"\n".join(p.warnings))
            self.viewed.add(c.id)
            self.update_buttons()

    def decide(self, decision):
        row = self.table.currentRow()
        if not 0 <= row < len(self.rows):
            return
        p, c = self.rows[row]
        if decision == "approved" and (c.status != "ready" or c.id not in self.viewed):
            QMessageBox.warning(self, "Подтверждение недоступно", "Сначала откройте изменения. Подтвердить можно только правку со статусом ready.")
            return
        try:
            self.logger.log("decision", title=p.snapshot.title, id=c.id, decision=decision)
        except OSError as exc:
            QMessageBox.warning(self, "Ошибка журнала", str(exc))
            return
        if decision=='approved': approve_change(p,c.id)
        else: reject_change(p,c.id)
        self.table.item(row,7).setText(self.decision_text(c))
        self.update_buttons()

    def export(self):
        path, _ = QFileDialog.getSaveFileName(self, "Экспортировать отчёт", "report.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(make_report(self.package, self.plans, self.failures,self.mode_box.currentData()), ensure_ascii=False, indent=2), encoding="utf-8")
                self.logger.log("report_exported", package_id=self.package["package_id"])
                self.status.setText(f"Отчёт сохранён: {path}")
            except OSError as exc:
                QMessageBox.warning(self, "Ошибка экспорта", str(exc))

    @staticmethod
    def decision_text(change):
        if change.status=='published': return 'Опубликовано'
        if change.status=='already_applied': return 'Уже применено'
        if change.status!='ready':
            return 'Требуется ручная проверка' if change.status in {'unsupported_structure','structure_change_required','entity_ambiguous','entity_unverified'} else 'Ошибка проверки'
        return {'pending':'Ожидает решения','approved':'Подтверждено','rejected':'Отклонено'}[change.decision]

    def refresh_status(self):
        self.article_status.setText('\n'.join(f"{p.snapshot.title}: "+('Опубликовано' if any(c.status=='published' for c in p.changes) else ('Требуется проверка: есть отклонённые операции/группы' if any(c.status not in {'ready','already_applied'} for c in p.changes) else 'Проверено; требуется индивидуальное подтверждение')) for p in self.plans))
        for row,(_,c) in enumerate(self.rows):
            self.table.item(row,6).setText(f'{c.error_layer}: {c.status}\n{c.message}')
            self.table.item(row,7).setText(self.decision_text(c))
        self.update_buttons()

    def mode_changed(self):
        from .transactions import clear_approvals
        for p in self.plans: clear_approvals(p)
        self.viewed.clear()
        self.refresh_status()

    def run_task(self,function,callback):
        if self.worker and self.worker.isRunning(): return
        self.worker=TaskWorker(function,self)
        self.worker.completed.connect(callback)
        self.worker.failed.connect(self.task_failed)
        self.worker.finished.connect(self.update_buttons)
        self.worker.start()
        self.update_buttons()

    def task_failed(self,code,message):
        layer='Авторизация' if code.startswith('authorization') else ('Публикация' if code in {'revision_conflict','edit_rejected','publication_unknown','recheck_failed'} else 'Выполнение')
        self.status.setText(f'{layer}: {code}: {message}')
        if not getattr(self.client,'authenticated_user',None): self.auth_label.setText('Не авторизован')
        self.viewed.clear()
        self.refresh_status()
        QMessageBox.warning(self,layer,message)

    def login(self):
        dialog=QDialog(self)
        dialog.setWindowTitle('BotPasswords — данные только в памяти')
        form=QFormLayout(dialog)
        form.addRow(QLabel('Создайте пароль бота на Специальная:BotPasswords. Не вводите основной пароль.'))
        username=QLineEdit(); password=QLineEdit(); password.setEchoMode(QLineEdit.EchoMode.Password)
        form.addRow('Имя: Участник@BrackelBot',username); form.addRow('Пароль бота',password)
        buttons=QDialogButtonBox(QDialogButtonBox.StandardButton.Ok|QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(dialog.accept);buttons.rejected.connect(dialog.reject);form.addWidget(buttons)
        if dialog.exec()!=QDialog.DialogCode.Accepted:
            password.clear();return
        user,secret=username.text().strip(),password.text();password.clear()
        self.status.setText('Авторизация…')
        self.run_task(lambda:self.client.login_botpassword(user,secret),self.logged_in)

    def logged_in(self,name):
        self.auth_label.setText('Авторизован: '+name)
        self.status.setText('Вход выполнен. Для тестовой публикации выберите Manual.')
        try:
            self.logger.log('authorization_succeeded',user=name)
        except OSError as exc:
            QMessageBox.warning(self, 'Ошибка журнала', str(exc))

    def logout(self):
        self.client.logout_local();self.auth_label.setText('Не авторизован');self.mode_changed()

    def publish(self):
        candidates=[p for p in self.plans if p.publishable and any(c.decision=='approved' for c in p.changes)]
        if self.mode_box.currentData()!=Mode.MANUAL.value or len(candidates)!=1:
            return
        publisher=Publisher(self.logger)
        self.status.setText('Повторная проверка ревизии и публикация утверждённых групп…')
        self.run_task(lambda:publisher.publish(candidates[0],self.client,mode=Mode.MANUAL),self.published)

    def published(self,result):
        self.status.setText(f"Сохранено в тестовой странице. Ревизия: {result['newrevid']}. Копия: {result['backup']}"+('\n'+result.get('audit_warning','')))
        self.refresh_status()

    def backup(self):
        snapshot=next((p.snapshot for p in self.plans if p.snapshot.title==TEST_TITLE),None)
        if snapshot:
            try:
                path=save_backup(snapshot);self.status.setText('Исходная версия сохранена: '+str(path))
                self.logger.log('backup_saved',title=snapshot.title,revid=snapshot.revid,path=str(path))
            except Exception as exc: QMessageBox.warning(self,'Резервная копия',str(exc))

    def restore(self):
        path,_=QFileDialog.getOpenFileName(self,'Восстановление: выберите копию тестовой страницы',str(default_backup_dir()),'JSON (*.json)')
        if path:
            self.status.setText('Получение текущей страницы для diff восстановления…')
            self.run_task(lambda:plan_restore(self.client,path),self.restored_preview)

    def restored_preview(self,plan):
        self.package={'package_id':'restore-'+str(plan.snapshot.revid),'schema_version':'1.1'}
        self.viewed.clear();self.checked([plan],[])
        self.status.setText('Просмотрите весь diff восстановления, подтвердите операцию и нажмите публикацию в Manual.')

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            self.status.setText("Дождитесь завершения проверки перед закрытием окна (тайм-аут одного запроса — 30 секунд).")
            event.ignore()
        else:
            event.accept()


def main():
    app = QApplication([])
    app.setApplicationName("WikipediaStatsUpdater")
    window = MainWindow()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
