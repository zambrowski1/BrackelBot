# SPDX-License-Identifier: MIT
from .errors import UpdateError
from .entity_resolver import EntityResolver
from .api_football_client import utcnow


class ClubRegistry:
    def __init__(self, store): self.store = store
    def get(self, team_id): return self.store.get('clubs',team_id)

    def confirm(self, team, qid, title, client):
        if type(team.get('id')) is not int or team['id']<=0 or team.get('national'):
            raise UpdateError('club_mismatch','Нужен положительный API ID клуба, не сборной')
        # The operator binds API ID. Name matching alone never creates a binding.
        resolved = EntityResolver(client).resolve({'name':team['name'],'kind':'club','wikidata_id':qid,'page_title':title})
        old = self.get(team['id'])
        if old and (old['qid']!=resolved.qid or old['title']!=resolved.title):
            raise UpdateError('registry_collision','Изменение связи клуба требует отдельного рассмотрения')
        for key, other in self.store.items('clubs'):
            if key!=str(team['id']) and other['qid']==resolved.qid:
                raise UpdateError('registry_collision','Разные API команды связаны с одним QID')
        record = {'api_id':team['id'],'name':team['name'],'qid':resolved.qid,'title':resolved.title,
                  'verified_at':utcnow()}
        self.store.put('clubs',team['id'],record)
        return record

    def verify(self, team_id, client):
        club = self.get(team_id)
        if not club: raise UpdateError('club_unmapped','API ID клуба не подтверждён')
        resolved = EntityResolver(client).resolve({'name':club['name'],'kind':'club','wikidata_id':club['qid'],'page_title':club['title']})
        return club, resolved
