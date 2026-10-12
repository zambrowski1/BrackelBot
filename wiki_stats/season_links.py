# SPDX-License-Identifier: MIT
"""Draft season-link formatter, starting in 2010; live coverage still needs review."""
from datetime import date
import re
from urllib.parse import quote
import mwparserfromhell as mw
from .errors import UpdateError
from .season_labels import normalize_table_season
from .wikitext_parser import WikitextParser
from .models import Patch
from .change_validator import apply_patches

ROOTS={
 'Чемпионат Германии по футболу':['Чемпионат Германии по футболу'],
 'Бундеслига':['Чемпионат Германии по футболу'],
 'Вторая Бундеслига':['Вторая Бундеслига'],
 'Третья лига Германии по футболу':['Третья лига Германии по футболу'],
 'Региональная лига Германии по футболу':['Региональная лига Германии по футболу'],
 **{name:['Региональная лига Германии по футболу'] for name in ['Региональная лига «Север»','Региональная лига «Запад»','Региональная лига «Юго-Запад»','Региональная лига «Бавария»']},
 'Английская Премьер-лига':['Чемпионат Англии по футболу'],
 'Премьер-лига (Англия)':['Чемпионат Англии по футболу'],
 'Чемпионат Италии по футболу (Серия A)':['Чемпионат Италии по футболу'],
 'Чемпионат Италии по футболу':['Чемпионат Италии по футболу'],
 'MLS':['MLS в сезоне','MLS'],
}


def season_rows(text):
 parser=WikitextParser(text)
 if not any(t.name=='клстат' for t in parser.templates):return []
 parser.club_table();rows=[];club=None;league=None;active=False
 for t in parser.templates:
  if t.name=='клстат':active=True
  elif active and t.name=='клстат/клуб':
   values=t.values();club=values['1'].text.strip();league=values['2'].text.strip() if '2' in values else None
  elif active and t.name=='клстат/лига':league=t.values()['1'].text.strip()
  elif active and t.name=='клстат/сезон':rows.append((club,league,t.values()['1']))
  elif active and t.name=='клстат/итогозаклуб':club=None;league=None
  elif active and t.name=='клстат/итого':break
 return rows


def candidates(league,season):
 links=mw.parse(league).filter_wikilinks()
 if len(links)!=1:raise UpdateError('season_league_unproven','Нужна однозначная ссылка на лигу')
 title=str(links[0].title).strip();key=normalize_table_season(season)
 full=key[:4]+'/'+str(int(key[:4])+1) if '/' in key else key
 roots=ROOTS.get(title,[title])
 return list(dict.fromkeys(root+' '+suffix for root in roots for suffix in dict.fromkeys([full,key]))),full


def verify_title(wiki,requested,allowed):
 page=wiki.get_page_identity(requested)
 if page.get('title') not in allowed:raise UpdateError('season_link_target_mismatch','Перенаправление ведёт не на нужный сезон лиги')
 if page.get('qid'):
  wd=wiki.get_wikidata_entity(page['qid'])
  if wd.get('sitelinks',{}).get('ruwiki',{}).get('title')!=page['title']:
   raise UpdateError('season_link_target_mismatch','Не подтвердилась обратная ссылка статьи сезона')
 return page['title']


def replacement(old,title,display):
 code=mw.parse(old);links=code.filter_wikilinks(recursive=False)
 if links:
  if len(links)!=1:raise UpdateError('unknown_season','Несколько ссылок в ячейке сезона')
  links[0].title=title;links[0].text=display
 else:
  found=False
  for node in code.nodes:
   if isinstance(node,mw.nodes.Text):
    raw=str(node)
    new,count=re.subn(r'(?<![0-9])(?:19|20)[0-9]{2}(?:[/–—-](?:(?:19|20)[0-9]{2}|[0-9]{2}))?(?![0-9])',lambda m:'[['+title+'|'+display+']]',raw,count=1)
    if count:code.replace(node,new);found=True;break
  if not found:raise UpdateError('unknown_season','Не найдена подпись сезона')
 return str(code)


def prepare(text,player,day,wiki):
 changes=[];issues=[];cache={}
 for club,league,value in season_rows(text):
  old=value.text.strip();key=normalize_table_season(old);year=int(key[:4])
  if not 2010<=year<=date.fromisoformat(day).year:continue
  try:
   titles,display=candidates(league,old);chosen=None
   for title in titles:
    if title not in cache:
     try:cache[title]=verify_title(wiki,title,titles)
     except UpdateError as exc:
      if exc.code not in {'entity_not_found','season_link_target_mismatch'}:raise
      cache[title]=None
    if cache[title]:chosen=cache[title];break
   if not chosen:
    issues.append({'code':'season_article_missing','league':league,'season':key});continue
   new=replacement(old,chosen,display)
   if new!=old:changes.append({'club':club,'league':league,'expected_wikitext':old,'page_title':chosen})
  except UpdateError as exc:issues.append({'code':'season_link_unproven','league':league,'season':key,'reason':exc.code})
 if not changes:return None,issues
 op={'id':'normalize-season-links','type':'normalize_season_links','group_id':'sofascore-reconciliation',
  'entity':{'name':player,'wikitext':'{{КлСтат}}'},'target':{'structure':'club_table'},'as_of':day,
  'sources':[{'url':'https://ru.wikipedia.org/wiki/'+quote(title.replace(' ','_'),safe='/'),
      'provenance':'user_provided','coverage_as_of':day,'note':'Существующая статья соответствующей лиги и сезона'} for title in dict.fromkeys(c['page_title'] for c in changes)],
  'payload':{'seasons':changes}}
 return op,issues


def execute(text,player,op,resolver):
 if op['entity']['name']!=player:raise UpdateError('player_mismatch','Таблица другого игрока')
 if resolver is None or not hasattr(resolver,'client'):raise UpdateError('entity_unverified','Нужна проверка статей сезонов')
 WikitextParser(text).infobox(player);rows=season_rows(text);patches=[];used=set()
 for change in op['payload']['seasons']:
  old=change['expected_wikitext'];key=normalize_table_season(old)
  if not 2010<=int(key[:4])<=date.fromisoformat(op['as_of']).year:raise UpdateError('season_invalid','Сезон вне разрешённого диапазона')
  titles,display=candidates(change['league'],old)
  if change['page_title'] not in titles:raise UpdateError('season_link_target_mismatch','Статья не соответствует лиге и сезону')
  title=verify_title(resolver.client,change['page_title'],titles);new=replacement(old,title,display)
  matching=[value for club,league,value in rows if club==change['club'] and league==change['league'] and value.text.strip() in {old,new}]
  if len(matching)!=1:raise UpdateError('old_value_mismatch','Изменилась ячейка сезона или её лига')
  value=matching[0]
  if value.start in used:raise UpdateError('patch_conflict','Повторная ячейка сезона')
  used.add(value.start)
  if value.text.strip()!=new:
   raw=value.text;prefix=raw[:len(raw)-len(raw.lstrip())];suffix=raw[len(raw.rstrip()):]
   patches.append(Patch(value.start,value.start+len(raw),raw,prefix+new+suffix,'season_link'))
 return apply_patches(text,patches,structural=True),'ready' if patches else 'already_applied'
