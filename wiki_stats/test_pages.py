# SPDX-License-Identifier: MIT
"""Exact sandbox allowlist; no prefixes, redirects or arbitrary subpages."""
TEST_TITLE = 'Участник:Zambrowski/testbot'
TEST_TITLES = (TEST_TITLE, *(TEST_TITLE + str(n) for n in range(1, 6)))

# The operator explicitly assigned these four sandbox copies to these players.
# This is an identity binding, not permission to fill or change profile fields.
TEST_PLAYER_BINDINGS = {
    TEST_TITLE+'2': {'wiki_name':'Грегор Кобель','title':'Кобель, Грегор','qid':'Q36184031','api_id':556866},
    TEST_TITLE+'3': {'wiki_name':'Тим Кляйндинст','title':'Кляйндинст, Тим','qid':'Q15627096','api_id':228981},
    TEST_TITLE+'4': {'wiki_name':'Давид Раум','title':'Раум, Давид','qid':'Q28540571','api_id':856076},
    TEST_TITLE+'5': {'wiki_name':'Вилли Орбан','title':'Орбан, Вилли','qid':'Q2577379','api_id':136924},
}


def is_test_page(title):
    return title in TEST_TITLES


def bound_test_player(title, mapping):
    expected = TEST_PLAYER_BINDINGS.get(title)
    return bool(expected and all(mapping.get(k)==v for k,v in expected.items()))
