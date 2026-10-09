# SPDX-License-Identifier: MIT
"""Prepare manual updates from an explicit article snapshot, without a stats API."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import json
from uuid import uuid4
import mwparserfromhell
from .errors import UpdateError
from .schema_validator import validate_package
from .wikitext_parser import WikitextParser
from .models import PageSnapshot


def load_article_snapshot(path):
    """Read one explicitly selected metadata/text pair, without scanning its folder."""
    path = Path(path)
    try:
        text_path = path.with_suffix('.wiki')
        if path.stat().st_size > 100000 or text_path.stat().st_size > 10000000:
            raise ValueError('Снимок превышает допустимый размер')
        metadata = json.loads(path.read_text(encoding='utf-8-sig'))
        title, revid, timestamp = metadata['title'], metadata['revid'], metadata['timestamp']
        if not isinstance(title, str) or not title.strip() or type(revid) is not int or revid < 1:
            raise ValueError('Нужны название статьи и положительный номер ревизии')
        datetime.fromisoformat(timestamp.replace('Z', '+00:00'))
        with text_path.open(encoding='utf-8', newline='') as stream:
            text = stream.read()
        return PageSnapshot(title, revid, timestamp, text, 2 if title == 'Участник:Zambrowski/testbot' else 0)
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise UpdateError('invalid_snapshot', f'Не удалось открыть снимок статьи: {exc}') from exc


@dataclass(frozen=True)
class StatRow:
    team: str
    wikitext: str
    structure: str
    field: str
    period: str
    category: str
    appearances: int
    goals: int

    @property
    def label(self):
        section = self.field if self.structure == 'career' else 'Таблица сезонов'
        return f'{section}: {self.team} · {self.period} · {self.category} · {self.appearances} ({self.goals})'


def article_rows(snapshot):
    parser = WikitextParser(snapshot.text)
    boxes = [t for t in parser.templates if t.name == 'футболист']
    if len(boxes) != 1:
        raise UpdateError('unsupported_structure', 'Нужна единственная карточка Футболист')
    values = boxes[0].values()
    player = values.get('имя')
    if player is None or not player.text.strip():
        raise UpdateError('player_mismatch', 'В карточке отсутствует имя футболиста')
    player = player.text.strip()
    rows, warnings = [], []
    for field in ('клубы', 'национальная сборная'):
        if field not in values:
            continue
        try:
            career = parser.career(field, player)
        except UpdateError as exc:
            warnings.append(f'{field}: {exc}')
            continue
        for period, team, value in career:
            try:
                numbers = value.numeric(pair=True)
                name = str(mwparserfromhell.parse(team).strip_code()).strip() or team
                rows.append(StatRow(name, team, 'career', field, period,
                                    'Чемпионат' if field == 'клубы' else 'Все матчи сборной',
                                    numbers[0].value, numbers[1].value))
            except UpdateError as exc:
                warnings.append(f'{field}, {period}: {exc}')
    if any(t.name == 'клстат' for t in parser.templates):
        try:
            table = parser.club_table()
            for row in table.seasons:
                name = str(mwparserfromhell.parse(row.club).strip_code()).strip() or row.club
                for index, category in enumerate(table.categories):
                    games = row.values[str(2 + 2 * index)].numeric()[0].value
                    goals = row.values[str(3 + 2 * index)].numeric()[0].value
                    rows.append(StatRow(name, row.club, 'club_table', '', row.season, category, games, goals))
        except UpdateError as exc:
            warnings.append(f'Таблица сезонов: {exc}')
    if not rows:
        raise UpdateError('unsupported_structure', 'Нет поддерживаемых строк с матчами и голами. ' + ' '.join(warnings))
    return player, rows, warnings


def build_manual_package(snapshot, row, appearances, goals, source_url, as_of, note='', *, bundesliga=False):
    player, available, _ = article_rows(snapshot)
    if row not in available:
        raise UpdateError('invalid_target', 'Выбранная строка отсутствует в исходном снимке')
    if not source_url.strip() or not as_of.strip():
        raise UpdateError('invalid_input', 'Укажите ссылку на источник и дату охвата данных (ГГГГ-ММ-ДД)')
    if snapshot.title != 'Участник:Zambrowski/testbot' and not bundesliga:
        raise UpdateError('invalid_input', 'Подтвердите, что игрок относится к сфере задачи: Бундеслига')
    target = {'structure': row.structure}
    if row.structure == 'career':
        target.update(field=row.field, period=row.period)
    identifier = 'manual-' + uuid4().hex
    operation = {
        'id': identifier, 'type': 'update_stats',
        'entity': {'name': row.team, 'wikitext': row.wikitext},
        'season': row.period if row.structure == 'club_table' else None,
        'competition': {'name': row.category, 'category': row.category if row.structure == 'club_table' else 'all',
                        'scope': 'category' if row.structure == 'club_table' else
                                 ('league' if row.field == 'клубы' else 'national_all')},
        'target': target, 'expected': {'appearances': row.appearances, 'goals': row.goals},
        'new': {'appearances': appearances, 'goals': goals}, 'as_of': as_of.strip(),
        'sources': [{'url': source_url.strip(), 'provenance': 'user_provided',
                     'coverage_as_of': as_of.strip(), 'note': note.strip()}],
    }
    package = {'schema_version': '1.1', 'package_id': identifier,
               'generated_at': datetime.now(timezone.utc).isoformat(),
               'articles': [{'title': snapshot.title, 'player': player,
                             'base_revid': snapshot.revid, 'operations': [operation]}]}
    if bundesliga:
        package['articles'][0]['subject_scope'] = 'bundesliga'
    validate_package(package)
    return package
