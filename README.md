# BrackelBot

Открытый Python-бот для подготовки обновлений футбольной статистики русской
Википедии. Серверный CLI получает API-Football, проверяет соответствия игроков
и клубов, строит diff и хранит состояние в ToolsDB на Wikimedia Toolforge.
В комплекте есть отдельный Windows GUI для ручной проверки.

Версия 0.4.0; Python 3.12+. Проект не аффилирован с Wikimedia или API-Football.
На реальном Toolforge пока не развёрнут; тариф API и запись на сервере
не проверены. Реализованные сценарии и ограничения: [IMPLEMENTATION_STATUS](docs/IMPLEMENTATION_STATUS.md).

## Установка серверного бота

```bash
git clone https://github.com/zambrowski1/BrackelBot.git
cd BrackelBot
python3 -m venv .venv-server
.venv-server/bin/python -m pip install -c requirements-tested-server.txt '.[server]'
.venv-server/bin/python -m wiki_stats.server_cli --help
.venv-server/bin/python -m wiki_stats.server_cli --state brackelbot.sqlite3 init-db
```

В Windows замените `python3` на `py -3`, `.venv-server/bin/python` на
`.venv-server/Scripts/python.exe`, либо запустите `TEST_SERVER.bat`.
Constraints фиксируют проверенные зависимости; версии и лицензии документированы.
SQLite используется локально; Toolforge запускает ToolsDB. Qt серверу не нужен.

Для локальной проверки установите `API_FOOTBALL_KEY` в окружении текущего shell
без сохранения в историю команд. На Windows `CHECK_API.bat` предлагает скрытый
ввод ключа. [local.env.example](examples/server/local.env.example) — справочник
переменных; программа не загружает env-файлы автоматически.

```bash
.venv-server/bin/python -m wiki_stats.server_cli check-api
.venv-server/bin/python -m wiki_stats.server_cli run --season 2026 --dry-run
```

Далее оператор подтверждает ID и карьерные базы по [SERVER_OPERATOR](docs/SERVER_OPERATOR.md).
Первый цикл может создать только очередь проверки. `check-api` читает API;
`--dry-run` читает источники и сохраняет локальное состояние, без правок Википедии.

## Wikimedia Toolforge

Полная последовательность — [TOOLFORGE.md](docs/TOOLFORGE.md): Tool Account,
SSH, клонирование, ToolsDB, Envvars, установка в контейнере, первое задание,
расписание, остановка и обновление. Конфигурация:

- [toolforge/jobs.yaml](toolforge/jobs.yaml): ежедневное задание в Dry Run.
- [bootstrap.sh](toolforge/bootstrap.sh): установка Python внутри job-контейнера.
- [run.sh](toolforge/run.sh), [operator.sh](toolforge/operator.sh): цикл и команды оператора.
- [toolforge.env.example](examples/server/toolforge.env.example): переменные без секретов.
- [anchor.TEST-ONLY.json](examples/server/anchor.TEST-ONLY.json): вымышленная карьерная база.
- [community-approval.DISABLED.json](examples/server/community-approval.DISABLED.json): выключенный пример одобрения.

Секреты задаются в Toolforge Envvars; `replica.my.cnf`, ключи, базы и рабочие
отчёты исключены из Git. Создание репозитория не развёртывает бота на Toolforge.

## Контроль публикации

По умолчанию публикация выключена, kill-switch включён. Ручные изменения
требуют просмотра diff и подтверждения отдельных операций. Test допускает
только `Участник:Zambrowski/testbot`; Automatic требует решения сообщества,
точного списка статей/сезонов/операций и включения оператором.
Бот проверяет исходную ревизию, однозначность ID, полноту API и структуру статьи.
Неизвестные форматы и спорные изменения направляются на ручную проверку.

## Тесты и структура

```bash
.venv-server/bin/python -m pip install -c requirements-tested-server.txt -e '.[server,test]'
.venv-server/bin/python -m pytest -q
```

Тесты используют локальные снимки и синтетические API-ответы, реальные правки
не выполняют. Без Qt пропускается модуль GUI. Проверки инфраструктуры Toolforge
выполняются отдельно. Инструкция разработки: [CONTRIBUTING.md](CONTRIBUTING.md).

`wiki_stats/` содержит полный серверный код, API-клиент, планировщик, ToolsDB/SQLite,
проверку изменений и публикацию; `tests/` — тесты и фикстуры; `examples/` —
конфигурационные и тестовые примеры; `toolforge/` — запуск; `docs/` — инструкции.
Windows GUI: установить `.[gui]` и открыть `START.bat`; подробности
в [WINDOWS_GUI](docs/WINDOWS_GUI.md), [SAFE_TESTING](docs/SAFE_TESTING.md).

## Лицензия и данные

Оригинальный код и документация — [MIT](LICENSE), OSI-approved лицензия,
удовлетворяющая требованию Toolforge к исходному коду:
[правила Toolforge](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Rules).
Библиотеки сохраняют собственные лицензии:
[проверка совместимости](docs/THIRD_PARTY_LICENSES.md), [уведомления и атрибуция](NOTICE.md).
Википедийные фикстуры и их производные остаются CC BY-SA 4.0.

API-Football не предоставляет универсальную открытую лицензию на публикацию
данных. Право на распространение статистики и соответствие Open Data Toolforge
нужно решить до эксплуатации: [DATA_POLICY](docs/DATA_POLICY.md).
В репозитории нет реальных выгрузок API. Открытый код не снимает требований к данным.

Связь: [GitHub Issues](https://github.com/zambrowski1/BrackelBot/issues);
оператор в Википедии — [Zambrowski](https://ru.wikipedia.org/wiki/Участник:Zambrowski).
Пароли и ключи в issues не отправляйте. Статус бота и развёртывание пока не получены.
