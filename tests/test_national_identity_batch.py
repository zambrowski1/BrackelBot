# SPDX-License-Identifier: MIT
from copy import deepcopy
import pytest
from wiki_stats.errors import UpdateError
from wiki_stats.national_team_identity import NationalTeamIdentity

CATALOGUE = {
    'aliases': {'Germany': 'Германия', 'GER': 'Германия', 'Hungary': 'Венгрия', 'HUN': 'Венгрия'},
    'countries': {
        'Германия': {'title': 'Сборная Германии по футболу', 'english_title': 'Germany national football team', 'gen': 'Германии'},
        'Венгрия': {'title': 'Сборная Венгрии по футболу', 'english_title': 'Hungary national football team', 'gen': 'Венгрии'},
    },
}
TEAMS = [dict(id=1, name='Germany', code='GER', gender='M', is_national=True, sport={'slug': 'football'}),
         dict(id=2, name='Hungary', code='HUN', gender='M', is_national=True, sport={'slug': 'football'})]


class Wiki:
    def __init__(self, problem=None): self.problem = problem; self.calls = []
    def _api(self, params, endpoint):
        self.calls.append((params, endpoint))
        pages = [{'title': title, 'ns': 0, 'pageprops': {'wikibase_item': 'Q1' if ('Germany' in title or 'Германии' in title) else 'Q2'}}
                 for title in params['titles'].split('|')]
        query = {'pages': pages}
        if 'en.wikipedia' in endpoint:
            if self.problem == 'missing': pages[-1]['missing'] = True
            elif self.problem == 'conflicting_qid': pages[-1]['pageprops']['wikibase_item'] = 'Q999'
            elif self.problem == 'duplicate_page': pages.append(deepcopy(pages[0]))
            elif self.problem == 'pages_object': query['pages'] = {'1': pages[0]}
            elif self.problem == 'redirect_loop':
                title = pages[0]['title']; query['redirects'] = [{'from': title, 'to': 'Alias'}, {'from': 'Alias', 'to': title}]
            elif self.problem == 'duplicate_redirect':
                title = pages[0]['title']; query['redirects'] = [{'from': title, 'to': title}, {'from': title, 'to': 'Alias'}]
            elif self.problem == 'valid_redirect':
                title = pages[0]['title']; pages[0]['title'] = 'Canonical country team'
                query['redirects'] = [{'from': title, 'to': pages[0]['title']}]
        if self.problem == 'invalid_qid':
            for page in pages: page['pageprops']['wikibase_item'] = True
        return {'query': query}


def test_batch_verifies_both_languages_and_reuses_cache_without_new_reads():
    wiki = Wiki('valid_redirect'); resolver = NationalTeamIdentity(wiki, CATALOGUE)
    resolver.prime(TEAMS)
    assert len(wiki.calls) == 2
    assert resolver.resolve(TEAMS[0])['wikidata_id'] == 'Q1'
    resolver.prime(TEAMS+TEAMS)
    assert len(wiki.calls) == 2 and len(resolver.cache) == 2


@pytest.mark.parametrize('problem', ['missing', 'conflicting_qid', 'duplicate_page', 'pages_object',
                                    'redirect_loop', 'duplicate_redirect', 'invalid_qid'])
def test_bad_batch_is_rejected_without_caching_any_country_in_that_batch(problem):
    resolver = NationalTeamIdentity(Wiki(problem), CATALOGUE)
    with pytest.raises(UpdateError): resolver.prime(TEAMS)
    assert resolver.cache == {}


@pytest.mark.parametrize('qid', [True, 1, 'Q0', 'Q01', 'Q-1', 'not-a-qid'])
def test_matching_but_invalid_qids_are_not_identity_proof(qid):
    class Individual:
        def get_page_identity(self, title): return {'title': title, 'qid': qid}
        def get_foreign_page_identity(self, title, language): return {'title': title, 'qid': qid}
    resolver = NationalTeamIdentity(Individual(), CATALOGUE)
    with pytest.raises(UpdateError): resolver.resolve(TEAMS[0])
    assert resolver.cache == {}
