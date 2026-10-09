# SPDX-License-Identifier: MIT
from PySide6.QtWidgets import (QDialog, QVBoxLayout, QFormLayout, QHBoxLayout,
                              QLabel, QLineEdit, QPushButton, QComboBox, QSpinBox,
                              QDialogButtonBox, QFileDialog, QCheckBox)
from .manual_entry import article_rows, build_manual_package, load_article_snapshot


class ManualStatsDialog(QDialog):
    def __init__(self, client, worker_factory, parent=None):
        super().__init__(parent)
        self.client, self.worker_factory = client, worker_factory
        self.worker = None
        self.snapshot = None
        self.package = None
        self.offline = False
        self.setWindowTitle('Ввести статистику')
        self.resize(850, 520)
        layout = QVBoxLayout(self)
        intro = QLabel('Выберите существующую строку статьи и введите проверенные числа. '
                       'API статистики не нужен. Источник подтверждаете вы; бот проверяет структуру и исходные значения.')
        intro.setWordWrap(True)
        layout.addWidget(intro)
        form = QFormLayout()
        self.title = QLineEdit()
        self.title.setPlaceholderText('Например: Кляйндинст, Тим')
        form.addRow('Название статьи', self.title)
        layout.addLayout(form)
        actions = QHBoxLayout()
        self.fetch_button = QPushButton('Получить из Википедии')
        self.local_button = QPushButton('Открыть локальный снимок')
        actions.addWidget(self.fetch_button)
        actions.addWidget(self.local_button)
        layout.addLayout(actions)
        form = QFormLayout()
        self.rows = QComboBox()
        form.addRow('Строка для обновления', self.rows)
        self.games, self.goals = QSpinBox(), QSpinBox()
        for box in (self.games, self.goals):
            box.setRange(0, 100000)
        form.addRow('Новое число матчей', self.games)
        form.addRow('Новое число голов', self.goals)
        self.source, self.coverage, self.note = QLineEdit(), QLineEdit(), QLineEdit()
        self.source.setPlaceholderText('https://… — страница, подтверждающая новые числа')
        self.coverage.setPlaceholderText('ГГГГ-ММ-ДД — дата охвата источника')
        form.addRow('Источник', self.source)
        form.addRow('Данные по состоянию на', self.coverage)
        form.addRow('Примечание', self.note)
        self.scope = QCheckBox('Игрок относится к сфере задачи: Бундеслига')
        form.addRow(self.scope)
        layout.addLayout(form)
        self.status = QLabel('Сначала откройте статью. Для локального снимка выберите JSON с ревизией; рядом должен быть одноимённый .wiki.')
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText('Подготовить правку')
        layout.addWidget(self.buttons)
        self.buttons.accepted.connect(self.prepare)
        self.buttons.rejected.connect(self.reject)
        self.fetch_button.clicked.connect(self.fetch)
        self.local_button.clicked.connect(self.open_local)
        self.rows.currentIndexChanged.connect(self.selected)
        self.title.textEdited.connect(self.clear_article)
        self.set_busy(False)

    def clear_article(self):
        self.snapshot = None
        self.rows.clear()
        self.scope.setChecked(False)
        self.source.clear()
        self.coverage.clear()
        self.note.clear()
        self.set_busy(False)
        self.status.setText('Название изменено. Откройте статью заново.')

    def set_busy(self, busy):
        for widget in (self.title, self.fetch_button, self.local_button, self.buttons.button(QDialogButtonBox.StandardButton.Cancel)):
            widget.setEnabled(not busy)
        for widget in (self.rows, self.games, self.goals):
            widget.setEnabled(not busy and self.snapshot is not None)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(not busy and self.rows.count() > 0)

    def fetch(self):
        title = self.title.text().strip()
        if not title:
            self.status.setText('Укажите название статьи.')
            return
        self.clear_article()
        self.offline = False
        self.worker = self.worker_factory(lambda: self.client.fetch_page(title), self)
        self.worker.completed.connect(self.loaded)
        self.worker.failed.connect(lambda code, message: self.status.setText(message))
        self.worker.finished.connect(lambda: self.set_busy(False))
        self.status.setText('Получение статьи…')
        self.set_busy(True)
        self.worker.start()

    def open_local(self):
        path, _ = QFileDialog.getOpenFileName(self, 'Снимок статьи', '', 'JSON (*.json)')
        if not path:
            return
        self.clear_article()
        try:
            self.offline = True
            self.loaded(load_article_snapshot(path))
        except Exception as exc:
            self.status.setText(f'Не удалось открыть снимок: {exc}')
        self.set_busy(False)

    def loaded(self, snapshot):
        try:
            player, rows, warnings = article_rows(snapshot)
            self.snapshot = snapshot
            self.title.setText(snapshot.title)
            for row in rows:
                self.rows.addItem(row.label, row)
            prefix = 'Локальный снимок, публикация отключена.' if self.offline else 'Статья получена из Википедии.'
            self.status.setText(f'{prefix} {player}, ревизия {snapshot.revid}.\n' + '\n'.join(warnings))
        except Exception as exc:
            self.snapshot = None
            self.status.setText(str(exc))

    def selected(self):
        row = self.rows.currentData()
        if row:
            self.games.setValue(row.appearances)
            self.goals.setValue(row.goals)

    def prepare(self):
        try:
            self.package = build_manual_package(self.snapshot, self.rows.currentData(), self.games.value(),
                                                self.goals.value(), self.source.text(), self.coverage.text(), self.note.text(),
                                                bundesliga=self.scope.isChecked())
        except Exception as exc:
            self.status.setText(str(exc))
            return
        self.accept()

    def reject(self):
        if not (self.worker and self.worker.isRunning()):
            super().reject()

    def closeEvent(self, event):
        if self.worker and self.worker.isRunning():
            event.ignore()
        else:
            event.accept()
