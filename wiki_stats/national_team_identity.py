# SPDX-License-Identifier: MIT
import json
import re
from importlib.resources import files
from .errors import UpdateError


def valid_qid(value):
    return isinstance(value,str) and re.fullmatch(r'Q[1-9][0-9]*',value) is not None


def identity_query(response):
    """Validate formatversion=2 pages and unambiguous redirect mappings."""
    query=response.get('query') if isinstance(response,dict) else None
    if not isinstance(query,dict) or not isinstance(query.get('pages'),list):
        raise UpdateError('national_identity_mismatch','Неверный формат ответа о сборной')
    index={}
    for page in query['pages']:
        if (not isinstance(page,dict) or not isinstance(page.get('title'),str)
                or type(page.get('ns')) is not int):
            raise UpdateError('national_identity_mismatch','Неверные данные страницы сборной')
        title=page['title']
        if title in index:
            raise UpdateError('national_identity_mismatch','Повторная страница сборной')
        index[title]=page
    mappings=[]
    for field in ('redirects','normalized'):
        rows=query.get(field,[])
        if not isinstance(rows,list):
            raise UpdateError('national_identity_mismatch','Неверные перенаправления сборной')
        mapping={}
        for row in rows:
            if (not isinstance(row,dict) or not isinstance(row.get('from'),str)
                    or not isinstance(row.get('to'),str) or row['from'] in mapping):
                raise UpdateError('national_identity_mismatch','Неоднозначное перенаправление сборной')
            mapping[row['from']]=row['to']
        mappings.append(mapping)
    index={title:page for title,page in index.items() if not page.get('missing') and page['ns']==0}
    return index,*mappings


class NationalTeamIdentity:
    def __init__(self,wiki,catalogue=None):
        self.wiki=wiki
        self.catalogue=catalogue or json.loads(files('wiki_stats').joinpath('national_teams.json').read_text(encoding='utf-8'))
        self.cache={}

    def text_identity(self,text):
        """Resolve editor links, football flags and plain countries against verified teams."""
        import mwparserfromhell as mw
        code=mw.parse(text)
        for tag in list(code.filter_tags()):
            if str(tag.tag).strip().casefold()=='ref':code.remove(tag)
        links=code.filter_wikilinks();templates=code.filter_templates()
        football=[t for t in templates if str(t.name).strip().casefold()=='футбол']
        others=[t for t in templates if t not in football and not str(t.name).strip().casefold().startswith('флаг ')]
        if others or len(links)>1 or len(football)>1 or links and football:
            raise UpdateError('national_identity_mismatch','Неоднозначная запись соперника в СбМатчи')
        if links:
            title=str(links[0].title).strip()
            if not title.startswith('Сборная ') or 'по футболу' not in title:
                raise UpdateError('national_identity_mismatch','Ссылка соперника не на футбольную сборную')
            for verified in self.cache.values():
                if verified['page_title']==title:return {'title':title,'qid':verified['wikidata_id']}
            return self.wiki.get_page_identity(title)
        if football:
            t=football[0]
            if not t.has('1') or any(str(p.name).strip().casefold() in {'жен','ж','пол'} and str(p.value).strip() for p in t.params):
                raise UpdateError('national_identity_mismatch','Не подтверждена категория сборной соперника')
            name=str(t.get('1').value).strip()
        else:name=code.strip_code().strip()
        aliases={k.casefold():v for k,v in self.catalogue['aliases'].items()}
        names={k.casefold():k for k in self.catalogue['countries']}
        country=names.get(name.casefold()) or aliases.get(name.casefold())
        if not country:raise UpdateError('national_identity_mismatch','Неизвестная сборная в записи матча: '+name)
        verified=self.cache.get(country)
        if verified is None:
            ref=self.catalogue['countries'][country]
            ru=self.wiki.get_page_identity(ref['title']);en=self.wiki.get_foreign_page_identity(ref['english_title'],'en')
            if not valid_qid(ru.get('qid')) or ru.get('qid')!=en.get('qid'):
                raise UpdateError('national_identity_mismatch','Не совпали статьи сборной соперника')
            verified={'page_title':ru['title'],'wikidata_id':ru['qid']}
        return {'title':verified['page_title'],'qid':verified['wikidata_id']}

    def country_name(self,team):
        if (type(team.get('id')) is not int or team['id']<=0 or team.get('is_national') is not True
                or team.get('gender')!='M' or (team.get('sport') or {}).get('slug')!='football'):
            raise UpdateError('national_identity_mismatch','Не подтверждена мужская футбольная сборная')
        name=self.catalogue['aliases'].get(team.get('name'))
        # Sofascore uses NIG for Nigeria; the generic catalogue uses it for Niger.
        code_name={'NIG':'Нигерия'}.get(team.get('code')) or self.catalogue['aliases'].get(team.get('code'))
        if not name or code_name and code_name!=name:
            raise UpdateError('national_identity_mismatch','Название и код сборной не подтверждены')
        return name

    def prime(self,teams):
        """Batch public identity reads; never turn a missing page into a guess."""
        names=sorted({self.country_name(team) for team in teams}-self.cache.keys())
        for offset in range(0,len(names),20):
            group=names[offset:offset+20]
            refs=[self.catalogue['countries'][name] for name in group]
            pages=[]
            for language,field in [('ru','title'),('en','english_title')]:
                response=self.wiki._api({'action':'query','prop':'pageprops','ppprop':'wikibase_item',
                    'redirects':1,'titles':'|'.join(ref[field] for ref in refs)},
                    endpoint=f'https://{language}.wikipedia.org/w/api.php')
                index,redirects,normalized=identity_query(response)
                resolved={}
                for ref in refs:
                    title=ref[field];target=normalized.get(title,title)
                    visited=set()
                    while target in redirects:
                        if target in visited:raise UpdateError('national_identity_mismatch','Циклическое перенаправление сборной')
                        visited.add(target);target=redirects[target]
                    page=index.get(target,{})
                    props=page.get('pageprops') or {}
                    if not isinstance(props,dict):
                        raise UpdateError('national_identity_mismatch','Неверные свойства страницы сборной')
                    resolved[title]={'title':page.get('title'),'qid':props.get('wikibase_item')}
                pages.append(resolved)
            verified={}
            for name,ref in zip(group,refs):
                ru,en=pages[0][ref['title']],pages[1][ref['english_title']]
                if ru['title']!=ref['title'] or not valid_qid(ru['qid']) or ru['qid']!=en['qid']:
                    raise UpdateError('national_identity_mismatch','Статьи сборной не совпали в двух языках: '+name)
                verified[name]={'name':name,'wikitext':name,'page_title':ru['title'],'wikidata_id':ru['qid'],
                    'gen':ref['gen'],'kind':'national_team','age':None}
            self.cache.update(verified)

    def resolve(self,team):
        name=self.country_name(team)
        if name in self.cache:return self.cache[name]
        reference=self.catalogue['countries'].get(name)
        if reference is None:raise UpdateError('national_identity_mismatch','Нет проверяемых статей сборной')
        ru=self.wiki.get_page_identity(reference['title'])
        en=self.wiki.get_foreign_page_identity(reference['english_title'],'en')
        if (not isinstance(ru,dict) or not isinstance(en,dict) or ru.get('title')!=reference['title']
                or not valid_qid(ru.get('qid')) or ru['qid']!=en.get('qid')):
            raise UpdateError('national_identity_mismatch','Русская и английская статьи относятся к разным сборным')
        result={'name':name,'wikitext':name,'page_title':ru['title'],'wikidata_id':ru['qid'],
                'gen':reference['gen'],'kind':'national_team','age':None}
        self.cache[name]=result
        return result
