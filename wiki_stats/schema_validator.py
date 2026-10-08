# SPDX-License-Identifier: MIT
import json
from datetime import date, datetime
from importlib.resources import files
from jsonschema import Draft202012Validator, FormatChecker
from .errors import UpdateError


def reject_credentials(value):
    if isinstance(value,dict):
        if any(str(k).casefold().replace('-','_') in {'password','token','lgpassword','lgtoken','csrf_token','bot_password','api_football_key','api_key','x_apisports_key','wiki_bot_password'} for k in value):
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
            if date.fromisoformat(op["as_of"]) > generated:
                raise UpdateError("invalid_input", "Дата охвата позже даты формирования пакета")
            if op["type"] in {"update_stats", "add_competition"} and (op["type"] == "update_stats" or package['schema_version'] == '1.1'):
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
                if op['type'] in {'add_club', 'add_national_team', 'update_career_period', 'update_current_club'} and op['target']['structure'] != 'career':
                    raise UpdateError('invalid_input', 'Эта операция относится к карточке')
                if op['type'] in {'add_season','update_totals'} and op['target']['structure'] != 'club_table':
                    raise UpdateError('invalid_input', 'Эта операция относится к клубной таблице')
                if op['type']=='add_season' and not op.get('season'):
                    raise UpdateError('invalid_input','Для нового сезона нужен season')
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
                    for cur in current:
                        if cur['entity'] != op['entity']:
                            raise UpdateError('invalid_input', 'Добавляемый клуб и текущий клуб в группе должны совпадать')
