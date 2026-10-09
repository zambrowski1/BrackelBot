# BrackelBot

Бот для обновления футбольной статистики в русской Википедии. Получает данные
из API-Football, сопоставляет игроков со статьями и готовит изменения карточек
и таблиц. Перед записью проверяет исходные числа и ревизию страницы.

Основной режим — серверный запуск на Wikimedia Toolforge. Для ручной работы
с формой ввода и JSON есть приложение для Windows; ключ статистического API ему не нужен.

Публикация по умолчанию выключена;
первый запуск только собирает данные и готовит предложения.

## Быстрый старт

Нужны Python 3.12+ и ключ API-Football.

```bash
git clone https://github.com/zambrowski1/BrackelBot.git
cd BrackelBot
python3 -m venv .venv-server
.venv-server/bin/python -m pip install -c requirements-tested-server.txt '.[server]'
.venv-server/bin/python -m wiki_stats.server_cli init-db
```

Задайте `API_FOOTBALL_KEY` в окружении, затем проверьте подписку и запустите сбор:

```bash
.venv-server/bin/python -m wiki_stats.server_cli check-api
.venv-server/bin/python -m wiki_stats.server_cli run --season 2026 --dry-run
```

В Windows используйте `py -3` вместо `python3`, а путь к Python —
`.venv-server/Scripts/python.exe`. Для проверки API можно просто открыть
`CHECK_API.bat`: ключ вводится скрыто и не записывается в файл.

Локально состояние хранится в SQLite, на Toolforge — в ToolsDB.
На первом запуске нужно подтвердить соответствия игроков, клубов и исходную
статистику. Это описано в руководстве оператора.

## Документация

| Что нужно сделать | Инструкция |
|---|---|
| Развернуть бота и настроить расписание | [Toolforge](docs/TOOLFORGE.md) |
| Подтвердить игроков, проверить diff и опубликовать изменения | [Работа с ботом](docs/SERVER_OPERATOR.md) |
| Запустить приложение для Windows | [Windows](docs/WINDOWS_GUI.md) |
| Подготовить JSON | [Формат пакета](docs/JSON_FORMAT.md), [операции](docs/JSON_OPERATIONS.md) |
| Проверить изменения на тестовой странице | [Тестирование](docs/SAFE_TESTING.md) |
| Создать пароль бота | [BotPasswords](docs/AUTHORIZATION.md) |
| Узнать, какие случаи ещё не поддерживаются | [Ограничения](docs/LIMITATIONS.md) |

В `examples/server/` лежат примеры настроек и исходной статистики,
в `toolforge/` — скрипты запуска и расписание. Файлы `*.env.example`
служат справочником: программа не загружает их автоматически.

Автоматические правки требуют одобрения ботозадачи и явного включения оператором.
Статьи, сезоны и разрешённые операции задаются отдельно. Для остановки публикации
есть команда `kill-switch on`.

## Разработка

```bash
.venv-server/bin/python -m pip install -c requirements-tested-server.txt -e '.[server,test]'
.venv-server/bin/python -m pytest -q
```

Серверные тесты работают без Qt и API-ключа. Для GUI установите extra `gui`.
[Как внести изменения](CONTRIBUTING.md). Ошибки и предложения — в
[Issues](https://github.com/zambrowski1/BrackelBot/issues).

## Лицензия

Код — [MIT](LICENSE). Снимки Википедии в тестах и производные примеры —
CC BY-SA 4.0; ссылки на авторов и ревизии находятся в [NOTICE.md](NOTICE.md).
[Лицензии библиотек](docs/THIRD_PARTY_LICENSES.md).

Права на публикацию полученной через API статистики пока не подтверждены.
Условия источника и требования Toolforge к данным описаны в
[DATA_POLICY.md](docs/DATA_POLICY.md).
