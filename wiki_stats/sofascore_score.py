# SPDX-License-Identifier: MIT
"""Keep shoot-out kicks separate from goals scored during the match."""


def match_score(row):
    try:
        scores=[row.get(side+'_score') or {} for side in ('home','away')]
        current=[s.get('current') for s in scores]
        if any(type(n) is not int or not 0<=n<=200 for n in current):return None
        if any(s.get('display') is not None and (type(s['display']) is not int or s['display']<0) for s in scores):return None
        periods=[s.get('periods') or {} for s in scores]
        if any(p.get(key) is not None and (type(p[key]) is not int or p[key]<0)
               for p in periods for key in ('normaltime','overtime')):return None
        penalties=[p.get('penalties') for p in periods]
        if penalties==[None,None]:
            if any(s.get('display',n)!=n for s,n in zip(scores,current)):return None
            return tuple(current)
        if (any(type(n) is not int or not 0<=n<=100 for n in penalties)
                or penalties[0]==penalties[1]):return None
        display=[s.get('display') for s in scores]
        periods_complete=all(type(p.get('normaltime')) is int and type(p.get('overtime')) is int for p in periods)
        if display==[None,None] and periods_complete:
            display=[p['normaltime']+p['overtime'] for p in periods]
        if any(type(n) is not int or not 0<=n<=100 for n in display) or display[0]!=display[1]:return None
        if periods_complete:
            if (any(p['normaltime']<0 or p['overtime']<0 for p in periods)
                    or display!=[p['normaltime']+p['overtime'] for p in periods]):return None
        combined=[n+p for n,p in zip(display,penalties)]
        if current!=display and current!=combined:return None
        return tuple(display)
    except (TypeError,AttributeError):return None
