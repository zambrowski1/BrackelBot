import json
from importlib.resources import files


def test_hoffenheim_reserve_names_do_not_resolve_to_first_team():
    clubs = json.loads(files('wiki_stats').joinpath('clubs.json').read_text(encoding='utf8'))['clubs']
    for name in ('1899 Hoffenheim II', 'TSG 1899 Hoffenheim II', 'TSG Hoffenheim II', 'TSG Hoffenheim II U23'):
        matches = [c for c in clubs if name.casefold() in {a.casefold() for a in [c['name'], c['title'], *c['aliases']]}]
        assert len(matches) == 1
        assert matches[0]['qid'] == 'Q16637410' and matches[0]['team_variant'] == 'reserve'
