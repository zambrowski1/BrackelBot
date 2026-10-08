# Установка на Toolforge

Для установки нужен Tool Account. Ниже используется имя `brackelbot`;
если у вас другое имя, замените его в путях и скриптах. Первый запуск — Dry Run.

## 1. Создать аккаунт инструмента

Откройте [официальный Quickstart](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Quickstart).
Войдите в [Toolsadmin](https://toolsadmin.wikimedia.org/), создайте developer account,
свяжите его с Wikimedia account и запросите доступ к Toolforge. После одобрения
создайте Tool Account `brackelbot`, если имя свободно. Добавьте свой SSH-ключ.
Запишите UNIX shell username: он нужен для подключения.

Tool Account запускает программу на сервере; Wikipedia account BrackelBot
выполняет правки. Это разные учётные записи.
Если имя инструмента занято, выберите другое и замените
`/data/project/brackelbot` в трёх shell-скриптах на фактическую домашнюю папку.

В терминале Windows:

```text
ssh ВАШ_SHELL_USERNAME@login.toolforge.org
become brackelbot
pwd
```

Рабочий каталог должен быть `/data/project/brackelbot`. Далее все команды
в этой инструкции выполняются на сервере под `tools.brackelbot`.

## 2. Скачать исходники

Под аккаунтом инструмента:

```bash
git clone https://github.com/zambrowski1/BrackelBot.git brackelbot
chmod u+x brackelbot/toolforge/*.sh
mkdir -p reports
toolforge jobs images
```

Перед получением API-данных прочитайте [DATA_POLICY.md](DATA_POLICY.md):
лицензия кода и права на данные — отдельные вопросы. Порядок копирования
разрешённого состояния для fork описан там же. Секреты задайте заново в своём
Tool Account. В каталоге инструмента укажите ссылку на репозиторий.

Shell-скрипты имеют LF (закреплено в .gitattributes). В этом проекте используется образ `python3.13`;
перед установкой убедитесь, что он действительно есть в выводе images.
Нужен Python не ниже 3.12. При смене образа создайте venv заново именно внутри
нового контейнера. Это соответствует [инструкции Python Jobs](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Python#Jobs).
Windows EXE и Qt на сервер переносить не нужно.

## 3. Создать постоянную базу ToolsDB

```bash
sql tools
```

В консоли MariaDB узнайте имя пользователя базы:

```sql
SELECT SUBSTRING_INDEX(CURRENT_USER(), '@', 1);
```

Если результат, например, `s12345`, выполните:

```sql
CREATE DATABASE s12345__brackelbot CHARACTER SET utf8mb4;
exit
```

Замените `s12345` на фактическое имя. Два подчёркивания обязательны.
Не добавляйте суффикс `_p`: база содержит операторские очереди.
Адрес и правила имён описаны в [ToolsDB](https://wikitech.wikimedia.org/wiki/Help:Toolforge/ToolsDB).
BrackelBot создаёт внутри таблицу `bb_state`: соответствия, базы статистики,
страницы API, очереди, отчёты, резервные копии и история правок.
SQLite предназначена для локальной разработки; серверный run.sh её запрещает.

## 4. Установить переменные

Каждая команда предложит ввести значение. Реальные секреты не вставляйте в
аргументы команд и не пересылайте вывод с их значениями. Envvars поступают
новому заданию при запуске, согласно [официальной инструкции](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Envvars_Service).

```bash
toolforge envvars create BRACKELBOT_STORAGE
toolforge envvars create BRACKELBOT_DB_NAME
toolforge envvars create API_FOOTBALL_KEY
toolforge envvars create BRACKELBOT_PUBLICATION
```

Вводимые значения соответственно: `toolsdb`, `s12345__brackelbot`, ваш API-ключ,
`0`. Пароль ToolsDB программа берёт из системных `TOOL_TOOLSDB_USER` и
`TOOL_TOOLSDB_PASSWORD`; в образах с общим хранилищем есть официальный резервный
вариант — файл `replica.my.cnf` инструмента. Его содержимое не копируется в проект.

Для Википедии позднее создайте BotPassword и установите:

```bash
toolforge envvars create WIKI_BOT_USERNAME
toolforge envvars create WIKI_BOT_PASSWORD
```

Имя — `BrackelBot@BrackelBot` либо имя другого явно выбранного тестового аккаунта
с суффиксом приложения. Подробнее: [AUTHORIZATION.md](AUTHORIZATION.md).

## 5. Подготовить Python и проверить подключения

```bash
toolforge jobs run bb-bootstrap --image python3.13 --command ./brackelbot/toolforge/bootstrap.sh --wait
toolforge jobs run bb-check-api --image python3.13 --command './brackelbot/toolforge/operator.sh check-api' --wait
toolforge jobs run bb-check-auth --image python3.13 --command './brackelbot/toolforge/operator.sh check-auth' --wait
```

Bootstrap ставит серверные зависимости без PySide6. Check-api показывает
подписку и действительные лимиты `/status`; check-auth только входит и проверяет
сессию, без публикации. Если одноразовое задание с таким именем уже существует,
удалите именно его (`toolforge jobs delete bb-check-api`) перед повторением.
Не используйте flush: он затронет другие задания.

## 6. Первый сбор данных

```bash
toolforge jobs run bb-first --image python3.13 --command './brackelbot/toolforge/run.sh --dry-run' --wait
toolforge jobs show bb-first
toolforge jobs logs bb-first
```

Отчёты лежат в `/data/project/brackelbot/reports` и независимо сохраняются в
ToolsDB. Первый цикл получает покрытие, клубы сезона, все страницы игроков и
текущие составы. Он создаёт очередь подтверждения ID. Отсутствие готовых правок
на первом запуске ожидаемо: требуется заполнение справочников и карьерных баз.
Дальше следуйте [SERVER_OPERATOR.md](SERVER_OPERATOR.md).

## 7. Ежедневное расписание

```bash
toolforge jobs load brackelbot/toolforge/jobs.yaml --job brackelbot-daily
toolforge jobs list
toolforge jobs show brackelbot-daily
```

Файл задаёт `0 4 * * *`, один запуск в сутки в 04:00 по времени планировщика,
Dry Run, без повторной записи при ошибке. В Jobs Framework проверьте отображаемое
расписание. Для другого времени измените поле schedule и снова загрузите файл.
Для запуска вне расписания: `toolforge jobs restart brackelbot-daily`.
Jobs и YAML описаны в [официальной документации](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Jobs_framework).

Если предыдущий цикл ещё идёт, второй остановится с `run_in_progress`.
Блокировка ToolsDB держится на соединении, снимается при его завершении и не
имеет таймера, позволяющего второму процессу начать во время долгого первого.
Флаг emails=onfailure просит инфраструктуру уведомлять о неуспешных заданиях.
Для уведомлений о каждом завершении оператор может выбрать emails=all.

## 8. Остановить, обновить и перезапустить

Остановить публикацию, сохранив сбор данных:

```bash
toolforge jobs run bb-emergency --image python3.13 --command './brackelbot/toolforge/operator.sh kill-switch on' --wait
```

Выключатель хранится в ToolsDB и проверяется перед каждым POST, включая уже
работающий цикл. Уже отправленный запрос отменить невозможно; проверьте историю.
Остановить само ежедневное задание:

```bash
toolforge jobs delete brackelbot-daily
```

Чтобы вернуть ежедневный Dry Run, загрузите jobs.yaml снова. При обновлении кода
сначала остановите задание, сохраните экспорт состояния и резервную копию базы,
выполните `git -C brackelbot pull --ff-only`, повторите bootstrap в нужном образе, затем верните расписание.
Состояние хранится в ToolsDB, а не во временной файловой системе контейнера.

## 9. Automatic после одобрения

До одобрения ничего в основном пространстве включать не надо. После реального
решения сообщества оператор заполняет копию `community-approval.DISABLED.json`:
ссылка на решение, дата, точные статьи, сезоны и типы разрешённых операций.
Команда `approval-install ... --confirm` сохраняет решение, указанное оператором;
достоверность ссылки она не проверяет. Пустой список разрешений или пример
с false не принимается.

Только после испытаний: установить одобрение, задать BRACKELBOT_PUBLICATION=1,
выполнить kill-switch off --confirm и заменить --dry-run на --automatic в jobs.yaml.
Подробные ограничения — [SERVER_OPERATOR.md](SERVER_OPERATOR.md). Программа не
включает этот режим и не расширяет разрешения самостоятельно.

## 10. Проверка установки

Проверьте, что задание видит Envvars и ToolsDB, получает нужный сезон,
сохраняет состояние после перезапуска и запускается по расписанию.
Перед рабочей публикацией выполните пробную запись на testbot.

## Лицензия и требования к исходникам

MIT LICENSE, NOTICE.md, полный код wiki_stats/, Toolforge-скрипты и примеры
должны оставаться доступны всем в репозитории. Сохраняйте источник и лицензию
каждого добавляемого компонента. Проверка библиотек: [THIRD_PARTY_LICENSES.md](THIRD_PARTY_LICENSES.md).
Не запускайте длительные циклы на bastion: используйте Jobs под Tool Account.
[Правила](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Rules) и
[Right to fork](https://wikitech.wikimedia.org/wiki/Help:Toolforge/Right_to_fork_policy).
