# SPDX-License-Identifier: MIT
"""AST-based addressing; offsets are calculated from serialization, never searched."""
from dataclasses import dataclass
import re
import mwparserfromhell as mw
from mwparserfromhell.nodes import Template, Text, Comment, Tag
from .errors import UpdateError
from .models import Patch


@dataclass
class TemplateRef:
    node: Template
    start: int

    @property
    def name(self):
        return str(self.node.name).strip().casefold()

    def values(self):
        offset = self.start + 2 + len(str(self.node.name))
        result = {}
        for p in self.node.params:
            offset += 1  # pipe
            if p.showkey:
                offset += len(str(p.name)) + 1
            key = str(p.name).strip()
            if key in result:
                raise UpdateError("ambiguous_target", f"Повторный параметр {key}")
            result[key] = ValueRef(p.value, offset)
            offset += len(str(p.value))
        return result


@dataclass
class ValueRef:
    code: object
    start: int

    @property
    def text(self):
        return str(self.code)

    def numeric(self, pair=False):
        nodes = self.code.nodes
        if not nodes or not isinstance(nodes[0], Text):
            raise UpdateError("unsupported_structure", "Число должно быть обычным текстом")
        for n in nodes[1:]:
            if isinstance(n, Comment) or (isinstance(n, Text) and not str(n).strip()):
                continue
            if isinstance(n, Tag) and str(n.tag).strip().casefold() == "ref" and not n.invalid:
                continue
            raise UpdateError("unsupported_structure", "Неизвестное оформление числового параметра")
        pattern = r"\s*([0-9]+)\s*\(\s*([0-9]+)\s*\)\s*" if pair else r"\s*([0-9]+)\s*"
        match = re.fullmatch(pattern, str(nodes[0]))
        if not match:
            raise UpdateError("unsupported_structure", "Поддерживаются только целые числа и пары матчей (голов)")
        return [NumberRef(int(match[g]), self.start + match.start(g), self.start + match.end(g), match[g])
                for g in range(1, 3 if pair else 2)]


@dataclass
class NumberRef:
    value: int
    start: int
    end: int
    text: str

    def patch(self, value: int, field: str):
        return Patch(self.start, self.end, self.text, str(value), field)


def top_templates(code, start=0):
    refs = []
    offset = start
    for node in code.nodes:
        if isinstance(node, Template):
            refs.append(TemplateRef(node, offset))
        offset += len(str(node))
    return refs


def only_template(value, name):
    templates = top_templates(value.code, value.start)
    if len(templates) != 1 or templates[0].name != name.casefold():
        raise UpdateError("unsupported_structure", f"Ожидался единственный шаблон {name}")
    if any(not isinstance(n, (Template, Comment)) and str(n).strip() for n in value.code.nodes):
        raise UpdateError("unsupported_structure", "Постороннее содержимое рядом с шаблоном")
    return templates[0]


@dataclass
class SeasonRow:
    club: str
    season: str
    values: dict[str, ValueRef]


@dataclass
class ClubTable:
    categories: list[str]
    seasons: list[SeasonRow]
    totals: dict[str, list[ValueRef]]
    grand_total: list[ValueRef]


class WikitextParser:
    def __init__(self, text):
        self.text = text
        self.code = mw.parse(text)
        if str(self.code) != text:
            raise UpdateError("unsupported_structure", "Парсер изменил исходный викитекст")
        self.templates = top_templates(self.code)

    def infobox(self, player):
        matches = [t for t in self.templates if t.name == "футболист"]
        if len(matches) != 1:
            raise UpdateError("unsupported_structure", "Нужна единственная карточка Футболист")
        box = matches[0].values()
        if "имя" not in box or box["имя"].text.strip() != player:
            raise UpdateError("player_mismatch", "Имя футболиста не совпадает с карточкой")
        return box

    def career(self, field, player):
        box = self.infobox(player)
        if field not in box:
            raise UpdateError("unsupported_structure", "В карточке нет нужного поля карьеры")
        values = only_template(box[field], "спортивная карьера").values()
        if not values or any(not k.isdigit() for k in values) or len(values) % 3:
            raise UpdateError("unsupported_structure", "Карьера должна содержать тройки период/команда/статистика")
        if set(values) != {str(i) for i in range(1, len(values) + 1)}:
            raise UpdateError("unsupported_structure", "Пропущенные позиционные параметры карьеры")
        return [(values[str(i)].text.strip(), values[str(i+1)].text.strip(), values[str(i+2)])
                for i in range(1, len(values) + 1, 3)]

    def club_table(self):
        headers = [t for t in self.templates if t.name == "клстат"]
        if len(headers) != 1:
            raise UpdateError("unsupported_structure", "Нужна единственная таблица КлСтат")
        header = headers[0]
        hp = header.values()
        if set(hp) != {str(i) for i in range(1, len(hp)+1)} or not 1 <= len(hp) <= 4:
            raise UpdateError("unsupported_structure", "Неизвестные параметры заголовка КлСтат")
        categories = [hp[str(i)].text.strip() for i in range(1, len(hp)+1)]
        if len(set(categories)) != len(categories):
            raise UpdateError("ambiguous_target", "Повторные категории соревнований")
        seasons, totals = [], {}
        club = None
        club_count = league_count = declared_club = declared_league = None
        grand = None
        active = False
        offset = 0
        allowed = {"клстат/клуб", "клстат/лига", "клстат/сезон", "клстат/итогозаклуб", "клстат/итого"}

        def count_param(params):
            if "сезоны" not in params or not re.fullmatch(r"[1-9][0-9]*", params["сезоны"].text.strip()):
                raise UpdateError("unsupported_structure", "Нужен явный положительный параметр сезоны")
            return int(params["сезоны"].text.strip())

        for node in self.code.nodes:
            ref = TemplateRef(node, offset) if isinstance(node, Template) else None
            offset += len(str(node))
            if ref and ref.start == header.start:
                active = True
                continue
            if not active:
                continue
            if isinstance(node, Comment) or (isinstance(node, Text) and not str(node).strip()):
                continue
            if not ref or ref.name not in allowed:
                raise UpdateError("unsupported_structure", "Неизвестный узел в таблице КлСтат")
            p = ref.values()
            if ref.name == "клстат/клуб":
                if club is not None or set(p) != {"1", "сезоны"}:
                    raise UpdateError("unsupported_structure", "Нарушена структура блока клуба")
                club = p["1"].text.strip()
                if club in totals:
                    raise UpdateError("ambiguous_target", "Повторный блок клуба")
                declared_club = count_param(p)
                club_count = 0
                league_count = declared_league = None
            elif ref.name == "клстат/лига":
                if club is None or set(p) != {"1", "сезоны"} or (declared_league is not None and league_count != declared_league):
                    raise UpdateError("unsupported_structure", "Нарушена структура лиги")
                declared_league = count_param(p)
                league_count = 0
            elif ref.name in {"клстат/сезон", "клстат/итогозаклуб"}:
                if club is None or declared_league is None or set(p) != {str(i) for i in range(1, 2*len(categories)+2)}:
                    raise UpdateError("unsupported_structure", "Неизвестная ширина строки таблицы")
                for i in range(2, 2*len(categories)+2):
                    p[str(i)].numeric()
                if ref.name == "клстат/сезон":
                    seasons.append(SeasonRow(club, p["1"].text.strip(), p))
                    club_count += 1
                    league_count += 1
                else:
                    if club_count != declared_club or league_count != declared_league:
                        raise UpdateError("unsupported_structure", "Количество сезонов не совпадает с оформлением таблицы")
                    totals[club] = [p[str(i)] for i in range(2, 2*len(categories)+2)]
                    club = None
            else:
                if club is not None or not totals or set(p) != {str(i) for i in range(1, 2*len(categories)+1)}:
                    raise UpdateError("unsupported_structure", "Нарушена строка итогов карьеры")
                grand = [p[str(i)] for i in range(1, 2*len(categories)+1)]
                for v in grand:
                    v.numeric()
                break
        if grand is None:
            raise UpdateError("unsupported_structure", "Нет закрывающей строки итогов КлСтат")
        keys = [(s.club, s.season) for s in seasons]
        if len(keys) != len(set(keys)):
            raise UpdateError("ambiguous_target", "Повторный сезон одного клуба")
        return ClubTable(categories, seasons, totals, grand)
