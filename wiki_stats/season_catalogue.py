# SPDX-License-Identifier: MIT
"""Exact league/season lookup in the saved, evidence-backed Wikipedia catalogue."""
from functools import lru_cache
from datetime import date
from pathlib import Path
import json,re

TITLE_SEASON=re.compile(r'(?<!\d)((?:19|20)\d{2})(?:[/–—-]((?:19|20)\d{2}|\d{2}))?(?!\d)')

def parse_season_title(title,start=2010,end=None):
 if end is None:end=date.today().year
 matches=list(TITLE_SEASON.finditer(title))
 if len(matches)!=1:return None
 match=matches[0];year=int(match[1])
 if not start<=year<=end:return None
 if match[2]:
  last=int(match[2]);expected=year+1 if len(match[2])==4 else (year+1)%100
  if last!=expected:return None
  season=f'{year}/{(year+1)%100:02}'
 else:season=str(year)
 prefix=title[:match.start()].strip(' —–-,')
 if prefix.endswith('(') and prefix.count('(')>prefix.count(')'):
  prefix=prefix[:-1].rstrip()
 prefix=re.sub(r'\s+(?:в сезоне|в сезон|сезон)$','',prefix,flags=re.I)
 suffix=title[match.end():].strip(' —–-(),')
 # A suffix identifies a division, stage or gender: keep it distinct.
 hint=(prefix+(' ('+suffix+')' if prefix and suffix else suffix)).strip()
 return (season,hint) if hint else None

@lru_cache(maxsize=1)
def load_catalogue():
 path=Path(__file__).with_name('season_catalogue.json')
 if not path.exists():return {'entries':[],'aliases':{}}
 return json.loads(path.read_text(encoding='utf8'))

def build_index(catalogue):
 index={}
 for row in catalogue.get('entries',[]):
  for key in row['league_keys']:
   index.setdefault((key,row['season']),set()).add(row['title'])
 return index

@lru_cache(maxsize=1)
def default_index():
 return build_index(load_catalogue())

def lookup(league_title,season,catalogue=None):
 index=default_index() if catalogue is None else build_index(catalogue)
 catalogue=load_catalogue() if catalogue is None else catalogue
 league_title=league_title.replace('_',' ').strip()
 groups=set(catalogue.get('aliases',{}).get(league_title,[]))
 if not groups:return []
 titles=sorted({title for key in groups for title in index.get((key,season),())})
 # Apertura/Clausura or several stages can share a parent league and year.
 # Their presence must not turn an arbitrary stage into the full-year link.
 return titles if len(titles)==1 else []
