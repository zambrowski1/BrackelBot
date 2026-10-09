# SPDX-License-Identifier: MIT
"""Operator-verified bindings; names are candidate discovery only."""
import re
from datetime import date
from .errors import UpdateError
from .api_football_client import utcnow
from .transactions import digest


def claim_values(entity, prop):
    values = []
    for claim in entity.get('claims',{}).get(prop,[]):
        if claim.get('rank') == 'deprecated': continue
        snak = claim.get('mainsnak',{})
        if snak.get('snaktype') == 'value':
            values.append(snak.get('datavalue',{}).get('value'))
    return values


def check_player_identity(client, mapping):
    if not re.fullmatch(r'Q[1-9][0-9]*',mapping.get('qid','')):
        raise UpdateError('identity_mismatch','Некорректный QID игрока')
    wd = client.get_wikidata_entity(mapping['qid'])
    title = wd.get('sitelinks',{}).get('ruwiki',{}).get('title')
    if not title:
        raise UpdateError('no_ru_article','У футболиста нет статьи русской Википедии')
    if title != mapping['title'] or client.get_page_identity(title)['qid'] != mapping['qid']:
        raise UpdateError('identity_mismatch','QID и русская статья футболиста не совпадают')
    humans = claim_values(wd,'P31')
    if not any(isinstance(x,dict) and x.get('id') == 'Q5' for x in humans):
        raise UpdateError('identity_mismatch','Объект не подтверждён как человек')
    birthdays = [x['time'][1:11] for x in claim_values(wd,'P569')
                 if isinstance(x,dict) and x.get('precision',0) >= 11 and isinstance(x.get('time'),str)]
    if not mapping.get('birth_date') or birthdays != [mapping['birth_date']]:
        raise UpdateError('identity_mismatch','Точная дата рождения API и Wikidata не совпадает однозначно')
    return wd


class PlayerRegistry:
    def __init__(self, store): self.store = store
    def get(self, player_id): return self.store.get('players',player_id)

    def confirm(self, player, qid, title, wiki_name, client):
        pid = player['id']
        if type(pid) is not int or pid <= 0 or not wiki_name.strip():
            raise UpdateError('registry_invalid','Нужны положительный API ID и имя карточки')
        birth = player.get('birth',{}).get('date')
        try: date.fromisoformat(birth)
        except (ValueError,TypeError):
            raise UpdateError('identity_mismatch','В API отсутствует точная дата рождения') from None
        mapping = {'api_id':pid,'qid':qid,'title':title,'wiki_name':wiki_name,
                   'birth_date':birth,'api_name':player.get('name',''),'club_ids':[],
                   'monitoring':'active','verified_at':utcnow(),'last_confirmed_statistics':{}}
        check_player_identity(client,mapping)
        for key, other in self.store.items('players'):
            if key != str(pid) and (other['qid']==qid or other['title']==title):
                raise UpdateError('registry_collision','Этот игрок уже связан с другим API ID; требуется разбор')
        previous = self.get(pid)
        if previous and (previous['qid'] != qid or previous['title'] != title):
            raise UpdateError('registry_collision','Изменение существующей связи требует отдельного рассмотрения')
        if previous:
            mapping['club_ids']=previous['club_ids']
            mapping['last_confirmed_statistics']=previous.get('last_confirmed_statistics',{})
        self.store.put('players',pid,mapping)
        return mapping

    def candidates(self, player, client):
        name = ' '.join(x for x in [player.get('firstname'),player.get('lastname')] if x) or player['name']
        data = client._api({'action':'wbsearchentities','search':name,'language':'en','type':'item','limit':5},
                           endpoint='https://www.wikidata.org/w/api.php')
        candidates = []
        for item in data.get('search',[]):
            wd = client.get_wikidata_entity(item['id'])
            candidates.append({'qid':item['id'],'title':wd.get('sitelinks',{}).get('ruwiki',{}).get('title'),
                               'label':item.get('label'), 'birth_claims':claim_values(wd,'P569')})
        # Never writes a binding. Even one candidate must be confirmed with API ID.
        self.store.put('review',digest(['identity',player['id']]),{'kind':'identity','status':'needs_review',
                       'api_id':player['id'],'name':name,'birth':player.get('birth'),'candidates':candidates})
        return candidates

    def confirm_anchor(self, player_id, anchor, client):
        from .statistics_engine import validate_anchor
        from .wikitext_parser import WikitextParser
        mapping = self.get(player_id)
        if not mapping: raise UpdateError('identity_unmapped','Сначала подтвердите связь игрока')
        check_player_identity(client,mapping)
        validate_anchor(anchor)
        club = self.store.get('clubs',anchor['team_id'])
        if not club: raise UpdateError('club_unmapped','Сначала подтвердите API ID клуба')
        from .entity_resolver import EntityResolver
        resolved = EntityResolver(client).resolve({'name':club['name'],'kind':'club','wikidata_id':club['qid'],'page_title':club['title']})
        snapshot = client.fetch_page(mapping['title'])
        if snapshot.title != mapping['title'] or snapshot.namespace != 0:
            raise UpdateError('identity_mismatch','База получена из другой статьи или пространства')
        rows = WikitextParser(snapshot.text).career('клубы',mapping['wiki_name'])
        matches = [v for p,c,v in rows if p==anchor['period'] and c==anchor['career_wikitext']]
        if len(matches)!=1 or [n.value for n in matches[0].numeric(pair=True)] != [anchor['career']['appearances'],anchor['career']['goals']]:
            raise UpdateError('old_value_mismatch','Карьерная база не соответствует единственной строке карточки')
        # Require the club's exact canonical link in the selected row.
        from .entity_resolver import reference_identity
        identity=reference_identity(client,anchor['career_wikitext'])
        if (identity['title'],identity['qid'])!=(resolved.title,resolved.qid):
            raise UpdateError('club_mismatch','Строка базы относится к другому клубу')
        anchor = {**anchor,'verified_at':utcnow(),'base_revid':snapshot.revid}
        self.store.put('anchors',f"{player_id}:{anchor['team_id']}:{anchor['season']}",anchor)
        mapping['club_ids']=sorted(set(mapping['club_ids'])|{anchor['team_id']})
        self.store.put('players',player_id,mapping)
        return anchor
