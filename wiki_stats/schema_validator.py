# SPDX-License-Identifier: MIT
import json
from datetime import date, datetime
from importlib.resources import files
from jsonschema import Draft202012Validator, FormatChecker
from .errors import UpdateError


def reject_credentials(value):
    if isinstance(value,dict):
        if any(str(k).casefold().replace('-','_') in {'password','token','lgpassword','lgtoken','csrf_token','bot_password','api_football_key','api_key','x_apisports_key','wiki_bot_password','highlightly_api_key','highlightly_keys','sofascore_api_key','sofascore_keys','x_rapidapi_key'} for k in value):
            raise UpdateError('invalid_input','Учётные данные запрещены в JSON обновлений')
        for child in value.values(): reject_credentials(child)
    elif isinstance(value,list):
        for child in value: reject_credentials(child)


def validate_package(package: dict) -> None:
    reject_credentials(package)
    if not isinstance(package,dict) or package.get('schema_version') not in {'1.0','1.1'}:
        raise UpdateError('invalid_input','Нужен JSON-объект версии 1.0 или 1.1')
    schema = json.loads(files("wiki_stats").joinpath("update.schema.json").read_text(encoding="utf-8"))
    branch = schema['oneOf'][0 if package['schema_version']=='1.0' else 1]
    selected = {**branch,'$defs':schema['$defs']}
    errors = list(Draft202012Validator(selected, format_checker=FormatChecker()).iter_errors(package))
    if errors:
        error = errors[0]
        raise UpdateError("invalid_input", f"{'/'.join(map(str, error.absolute_path))}: {error.message}")
    titles, ids = set(), set()
    generated = datetime.fromisoformat(package["generated_at"].replace("Z", "+00:00")).date()
    for article in package["articles"]:
        if article["title"] in titles:
            raise UpdateError("invalid_input", "Повторное название статьи")
        titles.add(article["title"])
        for op in article["operations"]:
            if op["id"] in ids:
                raise UpdateError("invalid_input", "Идентификаторы операций должны быть уникальны")
            ids.add(op["id"])
            if op['type']=='bind_infobox_name' and (article.get('base_revid') is None or not op['entity'].get('wikidata_id')):
                raise UpdateError('invalid_input','Для имени старой карточки нужны base_revid и проверенный QID')
            if op['type']=='repair_sbmatches':
                from .test_pages import is_test_page
                if (not is_test_page(article['title']) and op.get('payload',{}).get('complete') is not True) or article.get('base_revid') is None:
                    raise UpdateError('invalid_input','Замена полигонной выборкой разрешена только тестовой странице с base_revid')
            if op['type']=='migrate_career_template':
                if article.get('base_revid') is None or op['target']['structure']!='career':
                    raise UpdateError('invalid_input','Для замены шаблона нужны base_revid и target.structure=career')
            if op['type']=='migrate_club_table' and (article.get('base_revid') is None or op['target']['structure']!='club_table'):
                raise UpdateError('invalid_input','Для переноса клубной таблицы нужны base_revid и club_table')
            if op['type'] in {'normalize_club_table','normalize_statistics_layout','normalize_club_flags'} and (article.get('base_revid') is None or op['target']['structure']!='club_table'):
                raise UpdateError('invalid_input','Для изменения оформления таблицы нужны base_revid и club_table')
            if op['type']=='update_career_period' and op.get('payload',{}).get('restore_unknown_end') and article.get('base_revid') is None:
                raise UpdateError('invalid_input','Восстановление неизвестного периода требует base_revid')
            if op['type'] in {'repair_career_duplicates','repair_duplicate_season'}:
                if article.get('base_revid') is None:
                    raise UpdateError('invalid_input','Исправление дубликатов требует base_revid')
                if op['type']=='repair_career_duplicates' and (op['target']['structure']!='career' or not op['target'].get('period') or not op['target'].get('field')):
                    raise UpdateError('invalid_input','Для дубликатов карьеры нужны поле и период')
                if op['type']=='repair_duplicate_season' and (op['target']['structure']!='club_table' or not op.get('season')):
                    raise UpdateError('invalid_input','Для дубликатов сезона нужны клубная таблица и сезон')
            if date.fromisoformat(op["as_of"]) > generated:
                raise UpdateError("invalid_input", "Дата охвата позже даты формирования пакета")
            if op["type"] in {"update_stats", "add_competition"} and (op["type"] == "update_stats" or package['schema_version'] == '1.1'):
                if op['target']['structure']!='club_table' and any(v is None for v in op['expected'].values()):
                    raise UpdateError('invalid_input','Неизвестные исходные числа допустимы только в ячейках КлСтат')
                if set(op["expected"]) != set(op["new"]):
                    raise UpdateError("invalid_input", "expected и new должны содержать одинаковые поля")
                target = op["target"]
                if target["structure"] == "career":
                    if not target.get("period") or "field" not in target:
                        raise UpdateError("invalid_input", "Для карьеры нужны period и field")
                    required_scope = "league" if target["field"] == "клубы" else "national_all"
                    if op["competition"]["scope"] != required_scope or op["competition"]["category"] != "all":
                        raise UpdateError("invalid_input", "Карточка: клуб — чемпионат, сборная — все матчи своей команды; category=all")
                    if op["season"] is not None:
                        raise UpdateError("invalid_input", "Карточка содержит период карьеры, season должен быть null")
                elif not op["season"] or op["competition"]["scope"] != "category":
                    raise UpdateError("invalid_input", "Для таблицы нужны сезон и scope=category")
            if op['type'] in {'update_sbmatch', 'add_sbmatch','repair_sbmatches','create_sbmatches'}:
                if article.get('base_revid') is None:
                    raise UpdateError('invalid_input', 'Для СбМатчи нужен base_revid')
                if op['entity'].get('kind') != 'national_team' or not op['entity'].get('wikitext'):
                    raise UpdateError('invalid_input', 'Для СбМатчи нужны сборная и точный entity.wikitext')
            if package['schema_version'] == '1.1':
                if not op['id'].strip() or not article['player'].strip():
                    raise UpdateError('invalid_input', 'Пустой идентификатор или имя')
                if op['type'] in {'add_club', 'add_national_team', 'update_current_club'}:
                    kind = 'national_team' if op['type'] == 'add_national_team' else 'club'
                    if op['entity'].get('kind') != kind:
                        raise UpdateError('invalid_input', 'Нужен явный вид объекта entity.kind')
                    if kind == 'national_team' and 'age' not in op['entity']:
                        raise UpdateError('invalid_input', 'Для сборной задайте age: null для основной или возраст')
                elif not op['entity'].get('wikitext'):
                    raise UpdateError('invalid_input', 'Для точного сопоставления нужен entity.wikitext')
                if op['type'] in {'add_club', 'add_national_team', 'update_career_period', 'update_current_club','update_shirt_number'} and op['target']['structure'] != 'career':
                    raise UpdateError('invalid_input', 'Эта операция относится к карточке')
                if op['type'] in {'add_season','update_totals','add_table_club','remove_season','remove_table_club','create_statistics_section','update_table_date'} and op['target']['structure'] != 'club_table':
                    raise UpdateError('invalid_input', 'Эта операция относится к клубной таблице')
                if op['type'] in {'add_season','add_table_club','remove_season'} and not op.get('season'):
                    raise UpdateError('invalid_input','Для нового сезона нужен season')
                if op['type']=='add_table_club' and op['entity'].get('kind')!='club':
                    raise UpdateError('invalid_input','Для блока клуба нужен entity.kind=club')
                if op['type'] in {'remove_season','remove_table_club'} and not op['payload']['reason'].strip():
                    raise UpdateError('invalid_input','Укажите подтверждённую причину удаления')
                if op['type']=='update_career_period' and (not op['target'].get('period') or not op['target'].get('field')):
                    raise UpdateError('invalid_input','Для периода нужны точные target.field и target.period')
                if op['type']=='update_current_club' and op['target'].get('field','клубы')!='клубы':
                    raise UpdateError('invalid_input','Текущий клуб нельзя направить в поле сборной')
                if op['type'] == 'add_club' and op['payload']['role'] == 'primary':
                    group = op.get('group_id')
                    companions = [x for x in article['operations'] if group and x.get('group_id') == group]
                    current = [x for x in companions if x['type'] == 'update_current_club']
                    if not current:
                        raise UpdateError('invalid_input', 'Основной новый клуб должен быть связан с update_current_club')
                    historical = '{{н.в.}}' not in op['payload']['period']
                    # Closed intermediate stints share the final current-club
                    # update. The editor checks their chronology and previous row.
                    final_clubs = [x for x in companions if x['type']=='add_club' and x['payload']['role']=='primary'
                                   and '{{н.в.}}' in x['payload']['period']]
                    if historical and any(cur['entity']==last['entity'] for cur in current for last in final_clubs):
                        continue
                    if not any(cur['entity'] == op['entity'] for cur in current):
                        raise UpdateError('invalid_input', 'Добавляемый клуб должен иметь соответствующее изменение текущего клуба в группе')
