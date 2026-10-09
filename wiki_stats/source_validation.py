# SPDX-License-Identifier: MIT
"""Validate roster and transfer evidence before making career decisions."""
from datetime import date
from .errors import UpdateError


def response_rows(record):
    try:
        rows = record['data']['response']
        if not isinstance(rows, list):
            raise ValueError()
        return rows
    except (KeyError, TypeError, ValueError):
        raise UpdateError('api_invalid_response', 'Нет подтверждённого списка данных API') from None


def roster_members(record, team_id):
    rows = response_rows(record)
    try:
        if len(rows) != 1 or type(rows[0]['team']['id']) is not int or rows[0]['team']['id'] != team_id:
            raise ValueError()
        players = rows[0]['players']
        if not isinstance(players, list) or not players:
            raise ValueError()
        ids = [player['id'] for player in players]
        if any(type(pid) is not int or pid <= 0 for pid in ids) or len(ids) != len(set(ids)):
            raise ValueError()
        return set(ids)
    except (KeyError, TypeError, ValueError):
        raise UpdateError('api_incomplete', 'Состав клуба отсутствует, неоднозначен или содержит повторные ID') from None


def transfer_history(record, player_id):
    rows = response_rows(record)
    # An explicitly empty response is valid. A profile missing its transfer list is not.
    if not rows:
        return []
    try:
        if len(rows) != 1 or type(rows[0]['player']['id']) is not int or rows[0]['player']['id'] != player_id:
            raise ValueError()
        history = rows[0]['transfers']
        if not isinstance(history, list):
            raise ValueError()
        for transfer in history:
            day = transfer['date']
            if not isinstance(day, str) or date.fromisoformat(day).isoformat() != day:
                raise ValueError()
            for direction in ('in', 'out'):
                tid = transfer['teams'][direction]['id']
                if type(tid) is not int or tid <= 0:
                    raise ValueError()
        return history
    except (KeyError, TypeError, ValueError):
        raise UpdateError('api_invalid_response', 'История переходов не подтверждает игрока, даты или клубы') from None
