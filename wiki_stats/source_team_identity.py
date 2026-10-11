# SPDX-License-Identifier: MIT
"""Resolve an unfamiliar source club through exact, reversible wiki identities."""
from .errors import UpdateError
from .entity_resolver import claim_ids


def resolve_source_team(team, wiki, resolver):
    country_flags={'DE':'Германии','FR':'Франции','IT':'Италии','ES':'Испании',
                   'NL':'Нидерландов','BE':'Бельгии','TR':'Турции','US':'США',
                   'MC':'Монако','CZ':'Чехии','PT':'Португалии'}
    country=team.get('country') or {}
    expected_flag=country_flags.get(country.get('alpha2'))
    def checked(resolved):
        if expected_flag and resolved.flag!=expected_flag:
            raise UpdateError('entity_mismatch','Страна клуба в источнике не совпадает с Wikidata')
        return resolved
    ref = {'name':team['name'], 'kind':'club'}
    try:
        return checked(resolver.resolve(ref))
    except UpdateError as exc:
        if exc.code != 'entity_ambiguous':raise
    cache = getattr(resolver, '_source_teams', None)
    if cache is None:
        cache = resolver._source_teams = {}
    name = team['name'].strip()
    if name in cache:return checked(cache[name])
    # These are exact encyclopedia titles/redirects, not a fuzzy name search.
    candidates = [name, name+' F.C.', name+' FC', name+' (football club)', 'FC '+name, 'AFC '+name]
    found = {}
    for index,title in enumerate(candidates):
        if index==4 and found:
            break
        try:
            page = wiki.get_foreign_page_identity(title, 'en')
            qid = page.get('qid')
            if not qid:continue
            wd = wiki.get_wikidata_entity(qid)
            if not claim_ids(wd, 'P31') & {'Q476028','Q15944511'}:continue
            if wd.get('sitelinks',{}).get('enwiki',{}).get('title') != page['title']:
                raise UpdateError('entity_mismatch', 'Обратная ссылка английской статьи клуба не совпадает')
            resolved=resolver.resolve({**ref, 'wikidata_id':qid})
            found[qid] = checked(resolved)
        except UpdateError as exc:
            if exc.code not in {'entity_not_found','unsupported_entity'}:raise
    if len(found) != 1:
        raise UpdateError('entity_ambiguous', 'Нет единственной подтверждённой статьи клуба '+name)
    result = next(iter(found.values()))
    cache[name] = result
    return result
