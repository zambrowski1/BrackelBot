from datetime import datetime, timezone
import pytest
from tests.test_sofascore import API, Wiki, Resolver, MINI, snapshot, match
from tests.test_season_aggregate_mode import MAPPING
from wiki_stats.sofascore_cycle import prepare_player
from wiki_stats.structural_editor import recompute_totals, execute_operation
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import arithmetic_warnings, references


def table(rows):
    return '{{КлСтат|Чемпионат|Кубки}}\n{{КлСтат/Клуб|Клуб|Лига|сезоны='+str(len(rows))+'}}\n'+ '\n'.join(rows)+'\n{{КлСтат/ИтогоЗаКлуб||—|—|—|—}}\n|}\n{{комментарии}}'


@pytest.mark.parametrize('metric', [14, -54])
def test_independent_season_totals_are_summed_without_filling_unknown_categories(metric):
    text=table([f'{{{{КлСтат/Сезон|2020/21|31|{metric}|—|—|игры=32|голы={metric}}}}}',
                '{{КлСтат/Сезон|2021/22|30|0|2|0}}'])
    result=recompute_totals(text)
    total=next(t for t in WikitextParser(result).templates if t.name=='клстат/итогозаклуб').values()
    assert total['игры'].numeric()[0].value==64
    assert total['голы'].numeric(signed=True)[0].value==metric
    assert total['4'].numeric()[0].value is None
    assert recompute_totals(result)==result
    assert not arithmetic_warnings(result)


def test_one_unknown_season_prevents_full_club_total():
    result=recompute_totals(table(['{{КлСтат/Сезон|2020/21|31|14|—|—|игры=32|голы=14}}',
                                  '{{КлСтат/Сезон|2021/22|30|0|—|—|игры=—|голы=—}}']))
    total=next(t for t in WikitextParser(result).templates if t.name=='клстат/итогозаклуб').values()
    assert 'игры' not in total or total['игры'].numeric()[0].value is None


def test_changing_component_invalidates_older_independent_total_and_preserves_reference():
    text='{{Футболист|имя=Кальвин Браккельман}}\n'+table(['{{КлСтат/Сезон|2020/21|31|14|—|—|игры=32<ref>Источник</ref>|голы=14}}'])
    text=recompute_totals(text)
    op={'type':'update_stats','entity':{'wikitext':'Клуб'},'target':{'structure':'club_table'},
        'season':'2020/21','competition':{'name':'Чемпионат','category':'Чемпионат'},
        'expected':{'appearances':31,'goals':14},'new':{'appearances':33,'goals':15}}
    result,status=execute_operation(text,'Кальвин Браккельман',op)
    assert status=='ready'
    row=WikitextParser(result).club_table().seasons[0]
    assert row.values['игры'].numeric()[0].value is None
    subtotal=next(t for t in WikitextParser(result).templates if t.name=='клстат/итогозаклуб').values()
    assert subtotal['игры'].numeric()[0].value is None
    assert references(result)==references(text)


@pytest.mark.parametrize('complete', [True,False])
def test_aggregate_collection_uses_absence_proof_but_never_an_incomplete_log(complete):
    class ClosedSeason(API):
        def seasons(self,pid):
            return {'player_id':pid,'tournament_seasons':[{'tournament':{'id':35},'seasons':[{'id':25,'year':'25/26'}]},
                                                        {'tournament':{'id':217},'seasons':[{'id':26,'year':'26/27'}]}]}
        def matches(self,*args,**kwargs):
            rows=[match(1,day='2024-01-01')]+[match(i,day='2025-10-01') for i in range(2,7)]+[match(7,day='2026-09-01')]
            for row in rows[1:6]:row['season']={'id':25,'year':'25/26'}
            return {'complete':complete,'matches':rows}
    original=MINI.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,evidence=prepare_player(snapshot(original),MAPPING,ClosedSeason(),Wiki(),Resolver(),statistics_since=0,now=datetime(2026,10,9,tzinfo=timezone.utc))
    row=next(r for r in WikitextParser(plan.preview).club_table().seasons if r.season=='2025/26')
    assert row.values['4'].numeric()[0].value==(0 if complete else None)
    assert any(r['code']=='zero_from_complete_match_history' for r in evidence['review']) is complete
