"""Rebuild the embedded national-team catalogue from Wikipedia Action API.

Only page names, template names and FIFA codes are retained, not article text.
English redirects are excluded: U18 redirects frequently point to U19 teams.
Run explicitly; this script makes read-only API requests and writes local files.
"""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
import json
import hashlib
from pathlib import Path
import re
import requests

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / 'wikipedia' / 'СбМатчи'
PAGE = 'Список мужских национальных сборных по футболу'
HEADERS = {'User-Agent': 'BrackelBot/0.4 (https://github.com/zambrowski1/BrackelBot; national-team catalogue)'}
AGES = [0, *range(15, 24)]
EXTRA = {
    'СССР':'СССР', 'СНГ':'СНГ', 'ГДР':'ГДР', 'Югославии':'Югославия',
    'Чехословакии':'Чехословакия', 'Сербии и Черногории':'Сербия и Черногория',
    'Саара':'Саар', 'Нидерландских Антильских островов':'Нидерландские Антильские острова',
    'Российской империи':'Российская империя', 'Гренландии':'Гренландия',
    'Джерси':'Джерси', 'Гернси':'Гернси', 'Острова Мэн':'Остров Мэн',
    'Каталонии':'Каталония', 'Страны Басков':'Страна Басков', 'Бретани':'Бретань',
    'Корсики':'Корсика', 'Сардинии':'Сардиния', 'Тибета':'Тибет',
    'Курдистана':'Курдистан', 'Падании':'Падания', 'Окситании':'Окситания',
    'Галисии':'Галисия', 'Астурии':'Астурия', 'Прованса':'Прованс',
    'Маршалловых Островов':'Маршалловы Острова',
}
RENAMES = {'Китайская Народная Республика':'Китай', 'Респ. Конго':'Республика Конго',
           'Гвиана':'Французская Гвиана', 'Сборная мира':'Мир'}
MANUAL_ALIASES = {
    'GER':'Германия', 'AUS':'Австралия', 'AUT':'Австрия', 'NED':'Нидерланды',
    'Южная Корея':'Республика Корея', 'Северная Корея':'КНДР', 'КНР':'Китай',
    'Тайвань':'Китайский Тайбэй', 'Белоруссия':'Беларусь', 'Киргизия':'Кыргызстан',
    'Чешская Республика':'Чехия', 'Северная Македония':'Северная Македония',
    'Македония':'Северная Македония', 'Соединённые Штаты Америки':'США',
    'Соединенные Штаты Америки':'США', 'Кот-д\'Ивуар':'Кот-д’Ивуар',
    'Кот-дʼИвуар':'Кот-д’Ивуар', 'Папуа-Новая Гвинея':'Папуа — Новая Гвинея',
    'Папуа Новая Гвинея':'Папуа — Новая Гвинея', 'Свазиленд':'Эсватини',
    'Демократическая Республика Конго':'ДР Конго', 'ДРК':'ДР Конго',
    'Центральноафриканская Республика':'ЦАР', 'Южно-Африканская Республика':'ЮАР',
    'ФРГ':'Германия', 'Нидерландские Антильские Острова':'Нидерландские Антильские острова',
}


def api(lang, **params):
    cache = ROOT.parents[1] / 'work' / 'sbmatches-api-cache' / str(date.today())
    cache.mkdir(parents=True,exist_ok=True)
    name = hashlib.sha256(json.dumps([lang,params],sort_keys=True).encode()).hexdigest()+'.json'
    saved = cache / name
    if saved.exists(): return json.loads(saved.read_text(encoding='utf-8'))
    response = requests.get(f'https://{lang}.wikipedia.org/w/api.php',
                            params={'format':'json', **params}, headers=HEADERS, timeout=40)
    response.raise_for_status()
    data = response.json()
    if 'error' in data:
        raise RuntimeError(data['error'])
    saved.write_text(json.dumps(data,ensure_ascii=False),encoding='utf-8')
    return data


def pages(lang, titles, **props):
    titles = sorted(set(titles))
    def batch(chunk):
        params = {'action':'query','titles':'|'.join(chunk), **props}
        result = {}
        while True:
            data = api(lang, **params)
            for page in data['query']['pages'].values():
                previous = result.setdefault(page['title'], {})
                # Continuation may add language links to the same page.
                links = previous.get('langlinks', []) + page.get('langlinks', [])
                previous.update(page)
                if links: previous['langlinks'] = links
            if 'continue' not in data: break
            params.update(data['continue'])
        return result
    chunks, chunk = [], []
    for title in titles:
        trial = requests.Request('GET',f'https://{lang}.wikipedia.org/w/api.php',
            params={'format':'json','action':'query','titles':'|'.join(chunk+[title]),**props}).prepare().url
        if chunk and (len(chunk)>=40 or len(trial)>6500):
            chunks.append(chunk); chunk=[]
        chunk.append(title)
    if chunk: chunks.append(chunk)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results = pool.map(batch, chunks)
        return {k:v for result in results for k,v in result.items()}


def lua(value):
    # JSON quoting of UTF-8 strings is also valid Lua string quoting here.
    return json.dumps(value, ensure_ascii=False)


def main():
    source = api('ru', action='parse',page=PAGE,prop='wikitext|revid')['parse']
    text = source['wikitext']['*']
    entries = dict(re.findall(r'\[\[(Сборная [^\]|\n]+ по футболу)\|([^\]\n]+)\]\]',text))
    entries.pop('Сборная страны по футболу',None)
    fifa_titles = set(re.findall(r'\[\[(Сборная [^\]|\n]+ по футболу)\|',
        text.split('== Сборные стран-членов континентальных федераций, но не ФИФА ==')[0]))
    fifa_titles.discard('Сборная страны по футболу')
    entries.update({'Сборная '+gen+' по футболу':label for gen,label in EXTRA.items()})
    senior = pages('ru', entries, prop='info|revisions|langlinks',rvprop='content',
                   rvslots='main',lllang='en',lllimit=500)
    countries, aliases = {}, {}
    for article, label in entries.items():
        page = senior.get(article,{})
        if 'missing' in page or 'redirect' in page or not page: continue
        name = RENAMES.get(label,label)
        gen = article[len('Сборная '):-len(' по футболу')]
        content = page['revisions'][0]['slots']['main']['*']
        code = re.search(r'\|\s*код ФИФА\s*=\s*([A-Z]{3})\b',content,re.I)
        english = next((x['*'] for x in page.get('langlinks',[]) if x['lang']=='en'),None)
        countries[name] = {'gen':gen,'ru':{'0':article},'foreign':{},'foreign_de':{},'english_main':english}
        if code:
            key = code[1].upper()
            previous = aliases.get(key)
            if not previous or article in fifa_titles:
                aliases[key] = name
            elif countries[previous]['ru']['0'] not in fifa_titles:
                # Historical reuse is ambiguous; require the country name.
                aliases.pop(key,None)
        if label!=name: aliases[label]=name
    print('Senior teams found:',len(countries),flush=True)

    candidates = {}
    for name, team in countries.items():
        base = team['english_main']
        if not base: continue
        candidates.setdefault(base,[]).append((name,0))
        match = re.match(r"(.+?)(?: men's)? national (football|soccer) team$",base)
        if not match: continue
        prefix, sport = match.groups()
        for age in range(15,24):
            variants = [f'{prefix} national under-{age} {sport} team']
            if "men's" in base:
                variants.append(f"{prefix} men's national under-{age} {sport} team")
            for title in variants: candidates.setdefault(title,[]).append((name,age))
    english = pages('en', candidates,prop='info|langlinks',lllang='ru',lllimit=500)
    for title,targets in candidates.items():
        page = english.get(title,{})
        if not page or 'missing' in page or 'redirect' in page: continue
        russian = next((x['*'] for x in page.get('langlinks',[]) if x['lang']=='ru'),None)
        for name,age in targets:
            countries[name]['foreign'][str(age)] = title
            if russian and age:
                countries[name]['ru'][str(age)] = russian
    print('Verified English pages:',sum(len(x['foreign']) for x in countries.values()),flush=True)

    # German Wikipedia has separate youth teams where English uses an umbrella
    # redirect. Only independent pages for the exact age are accepted here too.
    german_senior = pages('ru',entries,prop='langlinks',lllang='de',lllimit=500)
    german_candidates = {}
    for name,team in countries.items():
        page = german_senior.get(team['ru']['0'],{})
        base = next((x['*'] for x in page.get('langlinks',[]) if x['lang']=='de'),None)
        if not base or 'Fußballnationalmannschaft' not in base: continue
        base = re.sub(r' \(Männer\)$','',base)
        for age in range(15,24):
            if str(age) in team['foreign']: continue
            for suffix in (f' (U-{age}-Junioren)',f' (U-{age})'):
                german_candidates.setdefault(base+suffix,[]).append((name,age))
    german = pages('de',german_candidates,prop='info|langlinks',lllang='ru',lllimit=500)
    for title,targets in german_candidates.items():
        page = german.get(title,{})
        if not page or 'missing' in page or 'redirect' in page: continue
        russian = next((x['*'] for x in page.get('langlinks',[]) if x['lang']=='ru'),None)
        for name,age in targets:
            countries[name]['foreign_de'][str(age)] = title
            if russian: countries[name]['ru'][str(age)] = russian
    print('Verified German pages:',sum(len(x['foreign_de']) for x in countries.values()),flush=True)

    expected = {f'Сборная {t["gen"]} по футболу (до {age} {"года" if age==21 else "лет"})':(n,age)
                for n,t in countries.items() for age in range(15,24)}
    russian = pages('ru',expected,prop='info')
    for title,(name,age) in expected.items():
        page = russian.get(title,{})
        if page and 'missing' not in page and 'redirect' not in page:
            countries[name]['ru'].setdefault(str(age),title)

    flag_titles = [f'Шаблон:Флаг {t["gen"]}' for t in countries.values()]
    flag_titles += [f'Шаблон:Флаг {n}' for n in countries]
    flag_titles += [f'Шаблон:Флаг {t["gen"].replace("Островов","островов")}' for t in countries.values()]
    flags = pages('ru',flag_titles,prop='info')
    for name,team in countries.items():
        team['flag']=None
        for ending in (team['gen'],name,team['gen'].replace('Островов','островов')):
            title='Шаблон:Флаг '+ending
            if 'missing' not in flags.get(title,{'missing':True}):
                team['flag']='Флаг '+ending; break
    print('Missing flags:',[n for n,t in countries.items() if not t['flag']],flush=True)
    # Do not collapse politically or historically distinct teams into one code.
    for key,name in MANUAL_ALIASES.items():
        if name in countries and key!=name: aliases[key]=name
    aliases = {k:v for k,v in aliases.items() if k not in countries and v in countries}
    metadata = {'checked':str(date.today()),'source':{'page':PAGE,'revision':source['revid']},
                'fifa_list_teams':sum(t['ru']['0'] in fifa_titles for t in countries.values()),
                'countries':dict(sorted(countries.items())),'aliases':dict(sorted(aliases.items()))}
    DEST.joinpath('сборные.json').write_text(json.dumps(metadata,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    write_module(metadata)


def write_module(metadata):
    lines=['-- BEGIN GENERATED COUNTRIES','-- Page names checked against Wikipedia Action API '+metadata['checked'],
           'local countries = {']
    for name,team in metadata['countries'].items():
        values=['gen='+lua(team['gen']), 'flag='+('nil' if not team['flag'] else lua(team['flag']))]
        for field in ('ru','foreign','foreign_de'):
            values.append(field+'={'+', '.join('['+str(k)+']='+lua(v) for k,v in sorted(team[field].items(),key=lambda x:int(x[0])))+'}')
        lines.append('    ['+lua(name)+']={'+', '.join(values)+'},')
    lines+=['}','local aliases = {']
    lines += ['    ['+lua(k)+']='+lua(v)+',' for k,v in metadata['aliases'].items()]
    lines+=['}','local normalizedAliases = {']
    normalized = {k.lower():k for k in metadata['countries']}
    normalized.update({k.lower():v for k,v in metadata['aliases'].items()})
    lines += ['    ['+lua(k)+']='+lua(v)+',' for k,v in sorted(normalized.items())]
    lines+=['}','-- END GENERATED COUNTRIES']
    path=DEST/'Модуль.lua'
    text=path.read_text(encoding='utf-8')
    if '-- BEGIN GENERATED COUNTRIES' in text:
        text=re.sub(r'-- BEGIN GENERATED COUNTRIES.*?-- END GENERATED COUNTRIES',lambda m:'\n'.join(lines),text,flags=re.S)
    else:
        start=text.index('-- Verified English articles;')
        end=text.index('\nlocal function ageCategory',start)
        text=text[:start]+'\n'.join(lines)+'\n'+text[end:]
    path.write_text(text,encoding='utf-8')


if __name__=='__main__': main()
