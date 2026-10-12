"""Complement category discovery with global Wikidata league-season relationships."""
from pathlib import Path
from datetime import datetime,timezone
import requests,json
query='''SELECT DISTINCT ?article ?season ?league ?leagueArticle ?country WHERE {
 {SELECT ?season ?article WHERE {
  ?season wdt:P31 wd:Q27020041.
  ?article schema:about ?season; schema:isPartOf <https://ru.wikipedia.org/>.
  FILTER(REGEX(STR(?article), "20(1[0-9]|2[0-6])"))
 }}
 ?season wdt:P3450 ?league.
 ?league wdt:P641 wd:Q2736.
 OPTIONAL {?leagueArticle schema:about ?league; schema:isPartOf <https://ru.wikipedia.org/>.}
 OPTIONAL {?league wdt:P17 ?country.}
}'''
years='('+'|'.join(str(year) for year in range(2010,datetime.now(timezone.utc).year+1))+')'
query=query.replace('20(1[0-9]|2[0-6])',years)
root=Path(__file__).resolve().parents[1]/'handoff/season-catalogue'
root.mkdir(parents=True,exist_ok=True)
response=requests.get('https://query.wikidata.org/sparql',params={'query':query,'format':'json'},headers={'User-Agent':'BrackelBot/0.4 (Wikipedia user Zambrowski; read-only season catalogue)'},timeout=55)
response.raise_for_status();data=response.json()
(root/'wikidata-query.rq').write_text(query,encoding='utf8')
(root/'wikidata-results.json').write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
print(len(data['results']['bindings']),flush=True)
