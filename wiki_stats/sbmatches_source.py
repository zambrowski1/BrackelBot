# SPDX-License-Identifier: MIT
"""Public profile citation; individual match evidence stays in the package."""
import re
from .errors import UpdateError


def profile_citation(payload):
    url=payload.get('source_url')
    if url is None:return ''
    if not isinstance(url,str) or not re.fullmatch(r'https://www\.sofascore\.com/football/player/(?:[^/\s|{}<>\[\]]+/)?[0-9]+',url):
        raise UpdateError('invalid_input','Нужна ссылка на профиль футболиста Sofascore')
    return '['+url+' Sofascore]'


def without_generated_match_links(citation):
    # Only bare generated links: keep authored references and labelled links.
    import mwparserfromhell as mw
    code=mw.parse(citation)
    for link in list(code.filter_external_links(recursive=False)):
        if link.title is None and re.fullmatch(r'https://www\.sofascore\.com/football/match/[^\s|{}<>\[\]]+',str(link.url)):
            code.remove(link)
    return str(code).strip()
