import pytest
from tests.test_klstat_editor import planned
from tests.stage23_helpers import MINI,TABLE_AUGSBURG,LEAGUE,season_operation
from tests.test_statistics_section import prepare,BARE
from tests.test_sofascore_national_cycle import TEXT,collect
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.change_validator import references,arithmetic_warnings
from wiki_stats.native_national_table import possible_national_statistics
from datetime import datetime,timezone
from tests.test_sofascore import API,Wiki,Resolver,match,snapshot
from tests.test_season_aggregate_mode import MAPPING
from wiki_stats.sofascore_cycle import prepare_player


@pytest.mark.parametrize('before',[False,True])
@pytest.mark.parametrize('crlf',[False,True])
def test_compact_club_header_expands_for_a_different_league_and_preserves_notes(before,crlf):
    old='{{КлСтат/Клуб|'+TABLE_AUGSBURG+'|сезоны=1}}\n{{КлСтат/Лига|'+LEAGUE+'|сезоны=1}}'
    annotated=LEAGUE+'<!-- league note --><ref>League source</ref>'
    text=MINI.replace(old,'{{КлСтат/Клуб|'+TABLE_AUGSBURG+'|'+annotated+'|сезоны=1}}')
    if crlf:text=text.replace('\n','\r\n')
    op=season_operation();op['payload']['league_wikitext']='[[Вторая Бундеслига]]'
    if before:
        op['season']='2024/25';op['payload']['insert_before_season']=op['payload'].pop('insert_after_season')
    result=planned([op],text)
    assert result.changes[0].status=='ready'
    assert result.preview.count('{{КлСтат/Лига|')==2
    assert len(WikitextParser(result.preview).club_table().seasons)==2
    assert references(result.preview)==references(text)
    assert '<!-- league note -->' in result.preview
    assert not arithmetic_warnings(result.preview)
    assert planned([op],result.preview).preview==result.preview


GOALS='=== Голы за сборную Германии ===\n{| class="wikitable"\n! # !! Дата !! Место !! Противник !! Счёт !! Результат !! Соревнование\n|-\n| 1 || 14 июня 2026 || Стадион || Люксембург || 1:0 || 4:0 || Отборочный турнир\n|}\n'


def test_national_goals_table_does_not_block_club_creation_or_allow_duplicate_nationals():
    text=BARE.replace('== Примечания ==',GOALS+'\n== Примечания ==')
    pkg,result=prepare(text)
    assert result.changes[0].status=='ready'
    assert GOALS in result.preview
    assert references(text)==[r for r in references(result.preview) if r in references(text)]
    assert possible_national_statistics(result.preview)


def test_unknown_national_table_still_blocks_automatic_club_creation():
    text=BARE.replace('== Примечания ==','=== Сборная ===\n{| class="wikitable"\n! Неизвестный показатель\n|-\n| 7\n|}\n== Примечания ==')
    _,result=prepare(text)
    assert result.changes[0].status=='statistics_exists'
    assert result.preview==text


@pytest.mark.parametrize('label',['[[Сборная Германии по футболу (до 19 лет)|Германия (до 19)]]',
                                  '[[Олимпийская сборная Германии по футболу|Германия (олимп.)]]'])
def test_unavailable_distinct_national_identity_does_not_block_verified_senior_card(label):
    text=TEXT.replace('|2020—{{н.в.}}','|2013—2014|'+label+'|3 (0)\n  |2020—{{н.в.}}')
    operations,issues,_=collect(text)
    assert len(operations)==1
    assert operations[0]['target']['period']=='2020—{{н.в.}}'
    assert not any(i['code']=='national_career_identity_unproven' for i in issues)


@pytest.mark.parametrize('before_arrival',[False,True])
def test_winter_arrival_includes_previous_season_only_if_all_appearances_belong_to_new_stint(before_arrival):
    from tests.test_sofascore import MINI as CARD
    class Winter(API):
        def seasons(self,pid):
            return {'player_id':pid,'statistics_types':{'35':{'25':['overall'],'26':['overall']}},
                    'tournament_seasons':[{'tournament':{'id':35},'seasons':[{'id':25,'year':'25/26'},{'id':26,'year':'26/27'}]}]}
        def transfers(self,pid):
            return {'player_id':pid,'transfers':[{'transfer_date':'2026-01-15','transfer_to':{'id':2600},'transfer_from':{'id':99}}]}
        def matches(self,*args,**kwargs):
            rows=[match(i,day='2025-12-01' if before_arrival else '2026-03-01') for i in range(1,6)]
            for row in rows:row['season']={'id':25,'year':'25/26'}
            rows.append(match(6));return {'complete':True,'matches':rows}
    text=CARD.split('{{КлСтат|')[0].replace('| имя =','| дата рождения = 22.08.1999\n| имя =')
    _,plan,_=prepare_player(snapshot(text),MAPPING,Winter(),Wiki(),Resolver(),statistics_since=0,now=datetime(2026,10,9,tzinfo=timezone.utc))
    numbers=WikitextParser(plan.preview).career('клубы',MAPPING['wiki_name'])[-1][2].numeric(pair=True)
    assert [v.value for v in numbers]==([4,1] if before_arrival else [10,4])
    if before_arrival:
        assert WikitextParser(plan.preview).infobox(MAPPING['wiki_name'])['обновление данных о клубе'].text==WikitextParser(text).infobox(MAPPING['wiki_name'])['обновление данных о клубе'].text
