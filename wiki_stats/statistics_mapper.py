# SPDX-License-Identifier: MIT
from .errors import UpdateError


def map_statistics(parser, player, op):
    parser.infobox(player)
    target = op["target"]
    entity = op["entity"]["wikitext"].strip()
    if target["structure"] == "career":
        rows = parser.career(target["field"], player)
        matches = [v for period, team, v in rows if period == target["period"].strip() and team == entity]
        if len(matches) != 1:
            raise UpdateError("ambiguous_target" if matches else "target_not_found", "Период и команда не определяют единственную запись карьеры")
        appearances, goals = matches[0].numeric(pair=True)
        return {"appearances": appearances, "goals": goals}
    table = parser.club_table()
    category = op["competition"]["category"]
    if category not in table.categories:
        raise UpdateError("structure_change_required", "Нет подходящей колонки; перестройка таблицы требует отдельного рассмотрения")
    if op["competition"]["name"] != category and not op["competition"].get("mapping_rule"):
        raise UpdateError("mapping_required", "Для соревнования нужно явное правило сопоставления; new содержит итог всей категории")
    matches = [s for s in table.seasons if s.club == entity and s.season == op["season"].strip()]
    if len(matches) != 1:
        raise UpdateError("ambiguous_target" if matches else "season_not_found", "Клуб и сезон не определяют единственную строку")
    index = table.categories.index(category)
    p = matches[0].values
    return {"appearances": p[str(2+2*index)].numeric()[0], "goals": p[str(3+2*index)].numeric()[0]}
