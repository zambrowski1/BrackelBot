# SPDX-License-Identifier: MIT
"""Compare explicitly covered seasons; never delete by absence in an API list."""
from copy import deepcopy
from .errors import UpdateError
from .change_planner import plan_article
from .klstat_editor import numbers, season_key, league_link
from .schema_validator import validate_package
from .structural_editor import table_layout
from .wikitext_parser import WikitextParser


def validate_evidence(evidence):
    import json
    from importlib.resources import files
    from jsonschema import Draft202012Validator, FormatChecker
    from .schema_validator import reject_credentials
    reject_credentials(evidence)
    definitions=json.loads(files('wiki_stats').joinpath('update.schema.json').read_text(encoding='utf-8'))['$defs']
    operation=definitions['operation']
    creation=next(c['then']['properties']['payload'] for c in operation['allOf']
                  if c['if']['properties']['type'].get('const')=='create_statistics_section')
    club=deepcopy(creation['properties']['clubs']['items'])
    # Existing blocks need exact wikitext, while newly created clubs also need kind/QID.
    club['properties']['entity']={'allOf':[{'$ref':'#/$defs/entity'},{'required':['wikitext']}]}
    club['properties']['insert_after_club']={'type':'string','minLength':1}
    metadata={k:deepcopy(creation['properties'][k]) for k in ['categories','statistics_as_of','full_career']}
    schema={'type':'object','additionalProperties':False,'$defs':definitions,
        'properties':{'player':{'type':'string','minLength':1},'as_of':{'type':'string','format':'date'},
                      'sources':operation['properties']['sources'],
                      'clubs':{'type':'array','minItems':1,'items':club},
                      'create_section':{'type':'object','additionalProperties':False,'properties':metadata,'required':list(metadata)}},
        'required':['player','as_of','sources','clubs']}
    error=next(iter(Draft202012Validator(schema,format_checker=FormatChecker()).iter_errors(evidence)),None)
    if error:
        raise UpdateError('invalid_input','Неверные данные КлСтат: '+('/'.join(map(str,error.absolute_path)) or 'корневой объект'))


def prepare_table(snapshot, evidence, resolver):
    validate_evidence(evidence)
    if not isinstance(evidence,dict) or not isinstance(evidence.get('clubs'),list) or not evidence['clubs']:
        raise UpdateError('incomplete_statistics','Нужен список подтверждённых клубов и сезонов')
    parser=WikitextParser(snapshot.text)
    if not any(t.name=='клстат' for t in parser.templates):
        creation=evidence.get('create_section')
        if not isinstance(creation,dict):
            raise UpdateError('incomplete_statistics','Для создания раздела нужны create_section: категории, дата статистики и full_career')
        payload={**deepcopy(creation),'complete':True,'clubs':[
            {'entity':deepcopy(b['entity']),'rows':deepcopy(b['rows'])} for b in evidence['clubs']]}
        op={'id':'create-klstat','type':'create_statistics_section','entity':{'name':'КлСтат','wikitext':'{{КлСтат}}'},
            'target':{'structure':'club_table'},'as_of':evidence['as_of'],'sources':deepcopy(evidence['sources']),
            'payload':payload}
        article={'title':snapshot.title,'player':evidence['player'],'base_revid':snapshot.revid,'operations':[op]}
        pkg={'schema_version':'1.1','package_id':'create-klstat-'+str(snapshot.revid),
             'generated_at':evidence['as_of']+'T23:59:59Z','articles':[article]}
        validate_package(pkg)
        return pkg,plan_article(snapshot,article,'1.1',resolver)
    table=parser.club_table()
    layouts=table_layout(parser)
    operations=[]
    group='klstat-reconciliation'
    base={'as_of':evidence['as_of'],'sources':deepcopy(evidence['sources']),'group_id':group,
          'target':{'structure':'club_table'}}
    def add(kind,entity,**fields):
        op={**deepcopy(base),'id':'klstat-'+str(len(operations)+1),'type':kind,'entity':deepcopy(entity),**fields}
        operations.append(op)
    # A bad grand total must not prevent independently covered row corrections.
    add('update_totals',{'name':'КлСтат','wikitext':'{{КлСтат}}'},payload={'complete':True})
    working=snapshot.text
    seen=set()
    for block in evidence['clubs']:
        entity=block['entity']; club=entity['wikitext'].strip()
        if club in seen:
            raise UpdateError('ambiguous_target','Повторный клуб в источнике')
        seen.add(club)
        rows=block['rows']
        if not isinstance(rows,list) or not rows:
            raise UpdateError('incomplete_statistics','Пустой список сезонов не подтверждает отсутствие игр')
        ordered=sorted(rows,key=lambda r:season_key(r['season']))
        if len({r['season'] for r in rows})!=len(rows):
            raise UpdateError('ambiguous_target','Повторный сезон в источнике')
        for row in ordered:
            payload={'categories':row['categories'],'league_wikitext':league_link(row['league_wikitext']),'complete':True}
            numbers(table,payload)
            current=table_layout(WikitextParser(working)).get(club)
            season=row['season']
            existing=next(((r,l) for r,l in current['seasons'] if r.values()['1'].text.strip()==season),None) if current else None
            if existing:
                ref,league=existing
                if league.values()['1'].text.strip()!=row['league_wikitext']:
                    raise UpdateError('league_mismatch','Существующий сезон относится к другой лиге; требуется проверка')
                for i,category in enumerate(table.categories):
                    old={'appearances':ref.values()[str(2+2*i)].numeric()[0].value,
                         'goals':ref.values()[str(3+2*i)].numeric()[0].value}
                    new=row['categories'][category]
                    if old!=new:
                        add('update_stats',entity,season=season,competition={'name':category,'category':category,'scope':'category'},
                            expected=old,new=new)
            elif not current:
                add('add_table_club',entity,season=season,payload={**payload,'insert_after_club':block['insert_after_club']})
            else:
                earlier=[r for r,l in current['seasons'] if season_key(r.values()['1'].text.strip())<season_key(season)]
                anchor=({'insert_after_season':earlier[-1].values()['1'].text.strip()} if earlier
                        else {'insert_before_season':current['seasons'][0][0].values()['1'].text.strip()})
                add('add_season',entity,season=season,payload={**payload,**anchor})
            # Each following row uses the staged structure, not the original offsets.
            article={'title':snapshot.title,'player':evidence['player'],'base_revid':snapshot.revid,'operations':operations}
            staged=plan_article(snapshot,article,'1.1',resolver)
            failures=[c for c in staged.changes if c.status not in {'ready','already_applied'}]
            if failures:
                failure=next((c for c in failures if c.status!='group_blocked'),failures[0])
                raise UpdateError(failure.status,failure.message)
            working=staged.preview
    # No delete operations are generated here: source coverage may be partial.
    package={'schema_version':'1.1','package_id':'klstat-'+str(snapshot.revid),
             'generated_at':evidence['as_of']+'T23:59:59Z','articles':[article]}
    validate_package(package)
    return package,plan_article(snapshot,article,'1.1',resolver)
