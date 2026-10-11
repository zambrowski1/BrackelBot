# SPDX-License-Identifier: MIT
from .dfb_club_profile import parse_closed_clubs
from .entity_resolver import reference_identity
from .errors import UpdateError
from .national_team_identity import valid_qid

CLUBS = {
    '1-fc-kaiserslautern': ('1. FC Kaiserslautern', '1. FC Kaiserslautern'),
    'energie-cottbus': ('Energie Cottbus', 'FC Energie Cottbus'),
    'energie-cottbus-ii': ('Energie Cottbus II', 'FC Energie Cottbus II'),
    'sc-freiburg': ('SC Freiburg', 'SC Freiburg'),
    'sc-freiburg-ii': ('SC Freiburg II', 'SC Freiburg II'),
    '1-fc-heidenheim': ('1. FC Heidenheim', '1. FC Heidenheim'),
    'borussia-moenchengladbach': ('Borussia Mönchengladbach', 'Borussia Mönchengladbach'),
}


def club_secondary_scopes(snapshot, mapping, api, wiki, table, now, *, goalkeeper=False):
    if not table or not hasattr(api, 'club_secondary_source'): return {}, {}, [], []
    bundle = api.club_secondary_source(mapping['api_id'])
    if bundle is None: return {}, {}, [], []
    try:
        if goalkeeper:
            raise UpdateError('dfb_club_keeper_unproven', 'Голы DFB не подтверждают пропущенные голы')
        scopes = parse_closed_clubs(mapping, bundle, now)
        from .dfb_reserve_identity import reserve_reference
        rows = {}; row_identities = {}
        for row in table.seasons:
            if bundle.get('reserve_identity_profiles') and reserve_reference(row.club):continue
            try:
                if row.club not in row_identities:
                    row_identities[row.club] = reference_identity(wiki, row.club)
                identity = row_identities[row.club]
                if not valid_qid(identity.get('qid')): continue
                rows.setdefault(identity['qid'], []).append((row, identity))
            except UpdateError:
                continue
        values, entities, issues, verified = {}, {}, [], []
        identities = {}; reserve_identities = {}
        for scope in scopes:
            if scope['team_slug']=='energie-cottbus-ii' and bundle.get('reserve_identity_profiles'):
                from .dfb_reserve_identity import energie_reserve_identity, reserve_reference
                selected=[r for r in table.seasons if r.season==scope['season'] and reserve_reference(r.club)]
                if not selected:continue
                if len(selected)!=1:
                    issues.append({'code':'dfb_reserve_row_ambiguous','season':scope['season']}); continue
                identity=energie_reserve_identity(scope,bundle,wiki)
                row=selected[0]; identity_key=identity['identity_key']
                scope={**scope,'sources':scope['sources']+identity['sources']}
                key=(identity_key,scope['season'],scope['category'])
                if key in values:raise UpdateError('dfb_club_scope_ambiguous','Неоднозначная резервная графа DFB')
                values[key]=scope
                entities[identity_key]={**identity,'wikitext':row.club}
                verified.append({**scope,'qid':None,'identity_key':identity_key,
                                 'parent_wikidata_id':identity['parent_wikidata_id']})
                if scope['category']=='Чемпионат':
                    from .dfb_reserve_cup import reserve_cup_zero
                    try:
                        cup=reserve_cup_zero(scope,identity,bundle,now)
                        if cup:
                            cup_key=(identity_key,scope['season'],cup['category'])
                            if cup_key in values:raise UpdateError('dfb_club_scope_ambiguous','Неоднозначная резервная кубковая графа')
                            values[cup_key]=cup
                            verified.append({**cup,'qid':None,'identity_key':identity_key,
                                             'parent_wikidata_id':identity['parent_wikidata_id']})
                    except UpdateError as exc:
                        issues.append({'code':exc.code,'message':str(exc),'season':scope['season']})
                continue
            known = CLUBS.get(scope['team_slug'])
            if not known or scope['team'] != known[0]:
                issues.append({'code':'dfb_club_identity_unproven','team':scope['team']}); continue
            if scope['team_slug'] not in identities:
                identities[scope['team_slug']] = wiki.get_foreign_page_identity(known[1], 'en')
            foreign = identities[scope['team_slug']]
            qid = foreign.get('qid')
            if not valid_qid(qid) or foreign.get('title') != known[1]:
                issues.append({'code':'dfb_club_identity_unproven','team':scope['team']}); continue
            selected = [(row, ident) for row, ident in rows.get(qid, [])
                        if row.season.replace(' (аренда)', '') == scope['season']]
            if len(selected) != 1: continue
            row, identity = selected[0]
            key = (qid, scope['season'], scope['category'])
            if key in values:
                raise UpdateError('dfb_club_scope_ambiguous', 'Неоднозначная графа DFB')
            values[key] = scope
            entities[qid] = {'name':identity['title'], 'wikidata_id':qid, 'wikitext':row.club}
            verified.append({**scope, 'qid':qid})
            if scope['team_slug']=='sc-freiburg-ii' and scope['category']=='Чемпионат' and bundle.get('freiburg_reserve_identity_profiles'):
                from .dfb_reserve_identity import freiburg_reserve_identity
                from .dfb_reserve_cup import reserve_cup_zero
                try:
                    reserve_key=(scope['team_slug'],scope['source_team_id'])
                    if reserve_key not in reserve_identities:
                        reserve_identities[reserve_key]=freiburg_reserve_identity(scope,bundle,wiki)
                    reserve=reserve_identities[reserve_key]
                    if qid!=reserve['wikidata_id']:
                        raise UpdateError('dfb_reserve_identity_unproven','Строка не соответствует резервной команде')
                    cup=reserve_cup_zero(scope,reserve,bundle,now)
                    if cup:
                        cup_key=(qid,scope['season'],cup['category'])
                        if cup_key in values:
                            raise UpdateError('dfb_club_scope_ambiguous','Неоднозначная резервная кубковая графа')
                        values[cup_key]=cup
                        verified.append({**cup,'qid':qid,'identity_key':qid,
                                         'parent_wikidata_id':reserve['parent_wikidata_id']})
                except UpdateError as exc:
                    issues.append({'code':exc.code,'message':str(exc),'season':scope['season']})
        return values, entities, issues, verified
    except UpdateError as exc:
        return {}, {}, [{'code':exc.code,'message':str(exc)}], []
