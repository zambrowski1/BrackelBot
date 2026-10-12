# SPDX-License-Identifier: MIT
"""Public profile citation; individual match evidence stays in the package."""
import re
from .errors import UpdateError


def profile_citation(payload,as_of=None):
    from datetime import date
    from .statistics_section import source_reference
    if not payload.get('source_url'):return ''
    accessed=payload.get('source_access_date') or as_of
    try:date.fromisoformat(accessed)
    except (ValueError,TypeError):raise UpdateError('invalid_input','Нужна дата обращения к источнику') from None
    return source_reference(payload,accessed)


def has_profile_reference(citation,url):
    import mwparserfromhell as mw
    for tag in mw.parse(citation).filter_tags():
        if str(tag.tag).strip().lower()!='ref':continue
        for template in tag.contents.filter_templates() if tag.contents else []:
            if str(template.name).strip().lower()=='cite web' and template.has('url') and str(template.get('url').value).strip()==url:return True
    return False


def merge_profile_citation(citation,payload,as_of):
    import mwparserfromhell as mw
    source=profile_citation(payload,as_of)
    if not source:return citation
    code=mw.parse(citation)
    for link in list(code.filter_external_links(recursive=False)):
        if (re.fullmatch(r'https://www\.sofascore\.com/football/player/(?:[a-z0-9-]+/)?[1-9][0-9]*',str(link.url))
                and str(link.url).rsplit('/',1)[-1]==payload['source_url'].rsplit('/',1)[-1]
                and str(link.title or '').strip()=='Sofascore'):code.remove(link)
    existing=str(code).strip()
    if has_profile_reference(existing,payload['source_url']):return existing
    return existing+source


def added_profile_references(before,after,payload,as_of):
    from .change_validator import references
    citation=profile_citation(payload,as_of)
    count=references(after).count(citation)-references(before).count(citation)
    return [citation]*max(0,count) if citation else []


def profile_url(snapshot,mapping):
    import mwparserfromhell as mw
    ending='/'+str(mapping['api_id'])
    for template in mw.parse(snapshot.text).filter_templates():
        if str(template.name).strip().lower()=='cite web' and template.has('url'):
            url=str(template.get('url').value).strip()
            if re.fullmatch(r'https://www\.sofascore\.com/football/player/(?:[a-z0-9-]+/)?[1-9][0-9]*',url) and url.endswith(ending):return url
    return 'https://www.sofascore.com/football/player/'+str(mapping['api_id'])


def without_generated_match_links(citation):
    # Only bare generated links: keep authored references and labelled links.
    import mwparserfromhell as mw
    code=mw.parse(citation)
    for link in list(code.filter_external_links(recursive=False)):
        if link.title is None and re.fullmatch(r'https://www\.sofascore\.com/football/match/[^\s|{}<>\[\]]+',str(link.url)):
            code.remove(link)
    return str(code).strip()
