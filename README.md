# BrackelBot

Бот для обновления футбольной статистики в русской Википедии. Получает данные
из Sofascore Scraper, Highlightly или API-Football, сопоставляет игроков со статьями и готовит изменения карточек
и таблиц. Перед записью проверяет исходные числа и ревизию страницы.

Основной режим — серверный запуск на Wikimedia Toolforge. Для ручной работы
с формой ввода и JSON есть приложение для Windows; ключ статистического API ему не нужен.

Публикация по умолчанию выключена;
первый запуск только собирает данные и готовит предложения.

## Быстрый старт

Нужны Python 3.12+ и ключ выбранного API.

```bash
git clone https://github.com/zambrowski1/BrackelBot.git
cd BrackelBot
python3 -m venv .venv-server
.venv-server/bin/python -m pip install -c requirements-tested-server.txt '.[server]'
.venv-server/bin/python -m wiki_stats.server_cli init-db
```

Для Highlightly задайте `BRACKELBOT_API_PROVIDER=highlightly` и
`HIGHLIGHTLY_API_KEY` в окружении. Можно настроить несколько ключей с учётом
общих квот подписок — [инструкция](docs/HIGHLIGHTLY.md). Затем:

```bash
.venv-server/bin/python -m wiki_stats.server_cli check-api
.venv-server/bin/python -m wiki_stats.server_cli search-player 'Harry Kane'
.venv-server/bin/python -m wiki_stats.server_cli run --season 2026 --dry-run
```

В Windows используйте `py -3` вместо `python3`, а путь к Python —
`.venv-server/Scripts/python.exe`. Для проверки API-Football можно открыть
`CHECK_API.bat`: ключ вводится скрыто и не записывается в файл.

Локально состояние хранится в SQLite, на Toolforge — в ToolsDB.
Для API-Football выберите `BRACKELBOT_API_PROVIDER=api_football` и задайте
`API_FOOTBALL_KEY`. На первом запуске нужно подтвердить соответствия игроков,
клубов и исходную статистику. Highlightly обновляет только подтверждённых игроков;
пустой реестр не запускает сканирование всей лиги.

Через API-Football для устаревшей карточки бот может подготовить пропущенную цепочку постоянных
переходов, включая промежуточные клубы других чемпионатов. Периоды и числа
проверяются по истории и сезонной статистике. Такое изменение требует просмотра
оператором; неизвестные значения не заменяются нулями.

## Документация

| Что нужно сделать | Инструкция |
|---|---|
| Развернуть бота и настроить расписание | [Toolforge](docs/TOOLFORGE.md) |
| Подключить Sofascore Scraper и получить общий diff карточки и КлСтат | [Sofascore](docs/SOFASCORE.md) |
| Подключить Highlightly и несколько ключей | [Highlightly](docs/HIGHLIGHTLY.md) |
| Подтвердить игроков, проверить diff и опубликовать изменения | [Работа с ботом](docs/SERVER_OPERATOR.md) |
| Запустить приложение для Windows | [Windows](docs/WINDOWS_GUI.md) |
| Подготовить JSON | [Формат пакета](docs/JSON_FORMAT.md), [операции](docs/JSON_OPERATIONS.md) |
| Проверить изменения на тестовой странице | [Тестирование](docs/SAFE_TESTING.md) |
| Создать пароль бота | [BotPasswords](docs/AUTHORIZATION.md) |
| Узнать, какие случаи ещё не поддерживаются | [Ограничения](docs/LIMITATIONS.md) |
| Посмотреть названия и ссылки всех команд трёх немецких лиг | [Справочник клубов](docs/CLUB_CATALOGUE.md) |
| Установить шаблон списка матчей футболиста за сборную | [СбМатчи](wikipedia/СбМатчи/README.md) |

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

Highlightly разрешает хранение и распространение данных в своих условиях;
для API-Football допустимость нашего сценария ещё нужно уточнить.
Условия источников и требования Toolforge к данным описаны в
[DATA_POLICY.md](docs/DATA_POLICY.md).
