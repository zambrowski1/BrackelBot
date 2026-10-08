# Проверка лицензий зависимостей

Проверено 2026-10-08 по METADATA и фактическим LICENSE/NOTICE установленных
дистрибутивов. Точные версии и ссылки на upstream — в
[серверном реестре](dependency-licenses-server.json) и
[реестре Windows](dependency-licenses-optional-windows.json). Оригинальные тексты
уведомлений сохранены в `third_party/`; они не изменялись и не стали MIT проекта.
Это аудит указанной среды, а не любых будущих версий или всех системных библиотек.

## Вывод для серверного кода

Прямые зависимости `mwparserfromhell`, `jsonschema`, `PyMySQL` — MIT,
`requests` — Apache-2.0. Транзитивные зависимости — MIT, BSD-3-Clause и MPL-2.0
(certifi). Совместное использование с собственным кодом MIT допустимо при
соблюдении отдельных лицензий. Нет зависимости, требующей перелицензирования
всего серверного BrackelBot под GPL. Код зависимостей в исходники бота не копируется.

MIT/BSD требуют сохранения уведомлений при распространении; Apache-2.0 также
требует лицензии/NOTICE, если предусмотрены, и отметок об изменениях при изменении
чужих файлов. Certifi остаётся MPL-2.0: при распространении его исполняемой
формы нужно указать доступ к соответствующим исходникам, а изменения его
покрытых файлов публиковать под MPL. [Mozilla FAQ](https://www.mozilla.org/en-US/MPL/2.0/FAQ/),
[исходники certifi](https://github.com/certifi/python-certifi).

Сервер устанавливается с extra `server`; тестовые зависимости не нужны в job.
`requirements-tested-server.txt` используется как constraints для воспроизводимых
версий. Python и системные компоненты образа имеют собственные лицензии и
распространяются поставщиком образа; этот проект не включает контейнерный образ.

## Сервер, транзитивные зависимости и тесты

| Пакет | Проверенная версия | Лицензия | Уведомление |
|---|---|---|---|
| attrs | 26.1.0 | MIT | [текст](../third_party/server/attrs/licenses/LICENSE) |
| certifi | 2026.7.22 | MPL-2.0 | [текст](../third_party/server/certifi/licenses/LICENSE) |
| charset-normalizer | 3.5.2 | MIT | [текст](../third_party/server/charset-normalizer/licenses/LICENSE) |
| colorama | 0.4.6 | BSD-3-Clause | [текст](../third_party/server/colorama/licenses/LICENSE.txt) |
| idna | 3.20 | BSD-3-Clause | [текст](../third_party/server/idna/licenses/LICENSE.md) |
| iniconfig | 2.3.1 | MIT | [текст](../third_party/server/iniconfig/licenses/LICENSE) |
| jsonschema | 4.26.0 | MIT | [текст](../third_party/server/jsonschema/licenses/COPYING) |
| jsonschema-specifications | 2025.9.1 | MIT | [текст](../third_party/server/jsonschema-specifications/licenses/COPYING) |
| mwparserfromhell | 0.7.2 | MIT | [текст](../third_party/server/mwparserfromhell/licenses/LICENSE) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [текст](../third_party/server/packaging/licenses/LICENSE) |
| pluggy | 1.6.0 | MIT | [текст](../third_party/server/pluggy/licenses/LICENSE) |
| Pygments | 2.21.0 | BSD-2-Clause | [текст](../third_party/server/Pygments/licenses/AUTHORS) |
| PyMySQL | 1.2.3 | MIT | [текст](../third_party/server/PyMySQL/licenses/LICENSE) |
| pytest | 9.1.1 | MIT | [текст](../third_party/server/pytest/licenses/LICENSE) |
| referencing | 0.37.0 | MIT | [текст](../third_party/server/referencing/licenses/COPYING) |
| requests | 2.34.2 | Apache-2.0 | [текст](../third_party/server/requests/licenses/LICENSE) |
| rpds-py | 2026.9.1 | MIT | [текст](../third_party/server/rpds-py/licenses/LICENSE) |
| urllib3 | 2.8.0 | MIT | [текст](../third_party/server/urllib3/licenses/LICENSE.txt) |

## Дополнительная среда Windows GUI / сборки

| Пакет | Проверенная версия | Лицензия | Уведомление |
|---|---|---|---|
| altgraph | 0.17.5 | MIT | [текст](../third_party/optional-windows/altgraph/LICENSE) |
| attrs | 26.1.0 | MIT | [текст](../third_party/optional-windows/attrs/licenses/LICENSE) |
| certifi | 2026.7.22 | MPL-2.0 | [текст](../third_party/optional-windows/certifi/licenses/LICENSE) |
| charset-normalizer | 3.5.2 | MIT | [текст](../third_party/optional-windows/charset-normalizer/licenses/LICENSE) |
| colorama | 0.4.6 | BSD-3-Clause | [текст](../third_party/optional-windows/colorama/licenses/LICENSE.txt) |
| idna | 3.20 | BSD-3-Clause | [текст](../third_party/optional-windows/idna/licenses/LICENSE.md) |
| iniconfig | 2.3.1 | MIT | [текст](../third_party/optional-windows/iniconfig/licenses/LICENSE) |
| jsonschema | 4.26.0 | MIT | [текст](../third_party/optional-windows/jsonschema/licenses/COPYING) |
| jsonschema-specifications | 2025.9.1 | MIT | [текст](../third_party/optional-windows/jsonschema-specifications/licenses/COPYING) |
| mwparserfromhell | 0.7.2 | MIT | [текст](../third_party/optional-windows/mwparserfromhell/licenses/LICENSE) |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause | [текст](../third_party/optional-windows/packaging/licenses/LICENSE) |
| pefile | 2024.8.26 | MIT | [текст](../third_party/optional-windows/pefile/LICENSE) |
| pluggy | 1.6.0 | MIT | [текст](../third_party/optional-windows/pluggy/licenses/LICENSE) |
| Pygments | 2.21.0 | BSD-2-Clause | [текст](../third_party/optional-windows/Pygments/licenses/AUTHORS) |
| pyinstaller | 6.22.3 | GPL-2.0-or-later WITH Bootloader-exception | [текст](../third_party/optional-windows/pyinstaller/licenses/COPYING.txt) |
| pyinstaller-hooks-contrib | 2026.8 | GPL-2.0-or-later (build hooks); Apache-2.0 (runtime hooks) | [текст](../third_party/optional-windows/pyinstaller-hooks-contrib/licenses/LICENSE) |
| PySide6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [текст](../third_party/optional-windows/PySide6/licenses/LicenseRef-Qt-Commercial.txt) |
| PySide6_Addons | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [текст](../third_party/optional-windows/PySide6_Addons/licenses/LicenseRef-Qt-Commercial.txt) |
| PySide6_Essentials | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [текст](../third_party/optional-windows/PySide6_Essentials/licenses/LicenseRef-Qt-Commercial.txt) |
| PySide6_Pdf | 6.12.0.140 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [текст](../third_party/optional-windows/PySide6_Pdf/licenses/LicenseRef-Qt-Commercial.txt) |
| PySide6_WebEngine | 6.12.0.140 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [текст](../third_party/optional-windows/PySide6_WebEngine/licenses/LicenseRef-Qt-Commercial.txt) |
| pytest | 9.1.1 | MIT | [текст](../third_party/optional-windows/pytest/licenses/LICENSE) |
| pywin32-ctypes | 0.2.3 | BSD-3-Clause | [текст](../third_party/optional-windows/pywin32-ctypes/LICENSE.txt) |
| referencing | 0.37.0 | MIT | [текст](../third_party/optional-windows/referencing/licenses/COPYING) |
| requests | 2.34.2 | Apache-2.0 | [текст](../third_party/optional-windows/requests/licenses/LICENSE) |
| rpds-py | 2026.9.1 | MIT | [текст](../third_party/optional-windows/rpds-py/licenses/LICENSE) |
| setuptools | 84.0.0 | MIT | [текст](../third_party/optional-windows/setuptools/licenses/LICENSE) |
| shiboken6 | 6.12.0 | LGPL-3.0-only OR GPL-2.0-only OR GPL-3.0-only | [текст](../third_party/optional-windows/shiboken6/licenses/LicenseRef-Qt-Commercial.txt) |
| urllib3 | 2.8.0 | MIT | [текст](../third_party/optional-windows/urllib3/licenses/LICENSE.txt) |

## GUI и распространяемые бинарные сборки

PySide6/Shiboken используют вариант LGPLv3; Qt имеет также сторонние компоненты
и некоторые модули только GPL. Собственный MIT-код GUI можно публиковать
с динамически подключаемыми LGPL-библиотеками, выполняя условия LGPL.
GUI использует QtCore/QtGui/QtWidgets; Qt вообще не импортируется сервером.
Исходный репозиторий не содержит EXE, Qt DLL или вендорных библиотек.

Публикация Windows EXE требует отдельного аудита реально включённых файлов:
сохранить LGPL/GPL/сторонние notices, обеспечить доступ к точным исходникам
библиотек и возможность замены LGPL-библиотек, не запрещать предусмотренную
лицензией отладку/обратную разработку. Наличие MIT LICENSE само по себе не
доказывает готовность ранее собранного EXE к распространению.
PyInstaller имеет исключение для bootloader; стандартные hooks-contrib — GPL,
runtime hooks — Apache-2.0. Эти инструменты не используются на Toolforge.
Реестр Windows описывает установленную среду, а не состав EXE.

Первичные источники:
[Qt licensing](https://doc.qt.io/qt-6/licensing.html),
[Qt for Python](https://doc.qt.io/qtforpython-6/),
[Qt third-party notices](https://doc.qt.io/qtforpython-6/licenses.html),
[LGPL obligations](https://www.qt.io/development/open-source-lgpl-obligations).

## Повторить проверку

В проверяемой среде запускайте `python scripts/audit_licenses.py . server`
или `python scripts/audit_licenses.py . optional-windows`. Скрипт собирает
METADATA и тексты уведомлений, не определяет совместимость автоматически:
новые/изменённые лицензии нужно прочитать и обновить этот документ.
Проверьте diff, измените constraints и запускайте соответствующие тесты.
Викитекст и API-данные обсуждаются отдельно в [NOTICE](../NOTICE.md) и
[DATA_POLICY](DATA_POLICY.md); лицензии Python-пакетов не дают прав на эти данные.

Полные тексты MPL-2.0, LGPL-3.0 и GPL-2.0/3.0 сохранены в
`third_party/license-texts/` из официального [SPDX license-list-data](https://github.com/spdx/license-list-data).
