# Лицензии и атрибуция

Код и оригинальная документация BrackelBot распространяются по MIT (LICENSE).
Лицензии библиотек и данных указаны отдельно ниже.

## Материалы Википедии

Полные снимки и извлечённые фрагменты в `tests/fixtures`, исходный testbot
и производные diff/backup-примеры в `examples` сохраняют CC BY-SA 4.0:
https://creativecommons.org/licenses/by-sa/4.0/ . Они исключены из MIT.
Атрибуция авторам осуществляется через ссылки на истории и фиксированные ревизии:

| Снимок | Ревизия | История авторов |
|---|---|---|
| Табакович, Харис | https://ru.wikipedia.org/w/index.php?oldid=155487220 | https://ru.wikipedia.org/w/index.php?title=Табакович,_Харис&action=history |
| Фюллькруг, Никлас | https://ru.wikipedia.org/w/index.php?oldid=155486931 | https://ru.wikipedia.org/w/index.php?title=Фюллькруг,_Никлас&action=history |
| Кляйндинст, Тим | https://ru.wikipedia.org/w/index.php?oldid=155488058 | https://ru.wikipedia.org/w/index.php?title=Кляйндинст,_Тим&action=history |
| Участник:Zambrowski/testbot | https://ru.wikipedia.org/w/index.php?oldid=155489454 | https://ru.wikipedia.org/w/index.php?title=Участник:Zambrowski/testbot&action=history |

Получены 2026-10-08; версии указаны также в JSON рядом с `.wiki`. Файлы
`fragments/*-club.wiki` извлечены из соответствующих полных снимков.
Примеры stage23 содержат изменённые тестовые числа и синтетические структуры;
они не подтверждают реальные спортивные сведения. Копии, фрагменты и изменения
викитекста распространяются с сохранением этой атрибуции и CC BY-SA 4.0.
Подробнее: [анализ статей](docs/ARTICLE_ANALYSIS.md), [формат примеров](docs/JSON_OPERATIONS.md).

## Библиотеки и внешние данные

Инвентаризация точных установленных версий и тексты уведомлений находятся в
[THIRD_PARTY_LICENSES.md](docs/THIRD_PARTY_LICENSES.md), `docs/dependency-licenses-*.json`
и `third_party/`. Внешние библиотеки не перелицензируются под MIT.
Исходный репозиторий не включает их исполняемые файлы.

API-Football — внешний сервис, его данные не получают MIT или CC BY-SA
от лицензии этого проекта. Условия использования и открытые данные Toolforge
обсуждаются в [DATA_POLICY.md](docs/DATA_POLICY.md).
