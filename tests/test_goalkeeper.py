from copy import deepcopy
import pytest
from wiki_stats.change_planner import plan_article
from wiki_stats.change_validator import arithmetic_warnings
from wiki_stats.schema_validator import validate_package
from wiki_stats.manual_entry import article_rows, build_manual_package
from wiki_stats.statistics_engine import normalize_dataset, career_from_anchor, update_operation
from wiki_stats.goalkeeper import decreased,is_goalkeeper
from wiki_stats.wikitext_parser import WikitextParser
from wiki_stats.errors import UpdateError
from stage23_helpers import MINI, snapshot, package, stats_operation, operation, TABLE_AUGSBURG
from server_helpers import ANCHOR, player_row, MAPPING
from wiki_stats.api_football_client import utcnow


@pytest.mark.parametrize('position',[
    'защитник <!-- бывший вратарь -->',
    'полузащитник <ref>Ранее играл как вратарь</ref>',
    '[[Защитник (футбол)|защитник]] <!-- goalkeeper -->',
    '[[Вратарь (футбол)|нападающий]]',
    'защитник <ref>Источник <ref>вратарь</ref></ref>',
])
def test_hidden_or_unshown_position_text_cannot_enable_conceded_statistics(position):
    text=MINI.replace('| имя =','| позиция = '+position+'\n| имя =')
    assert not is_goalkeeper(WikitextParser(text),'Кальвин Браккельман')


def test_hidden_goalkeeper_role_in_lead_is_not_a_role_declaration():
    lead="'''Кальвин Браккельман''' — [[футбол]]ист клуба. <!-- — [[футбол]]ист, [[Вратарь (футбол)|вратарь]] -->"
    text=MINI.replace('Текст 4 (1)',lead)
    assert not is_goalkeeper(WikitextParser(text),'Кальвин Браккельман')


def test_actual_visible_keeper_position_survives_comments_and_references():
    text=MINI.replace('| имя =','| позиция = [[Вратарь (футбол)|вратарь]] <!-- основной --> <ref>Защитник сыграл ранее</ref>\n| имя =')
    assert is_goalkeeper(WikitextParser(text),'Кальвин Браккельман')


@pytest.mark.parametrize('position',['','<!-- старая позиция -->','<ref>Источник</ref>'])
def test_empty_position_does_not_hide_legacy_keeper_role(position):
    text=MINI.replace('| имя =','| позиция = '+position+'\n| амплуа = вратарь\n| имя =')
    assert is_goalkeeper(WikitextParser(text),'Кальвин Браккельман')


def test_visible_position_has_priority_over_legacy_role():
    text=MINI.replace('| имя =','| позиция = защитник\n| амплуа = вратарь\n| имя =')
    assert not is_goalkeeper(WikitextParser(text),'Кальвин Браккельман')


def keeper_text():
    return MINI.replace('| имя =', '| позиция = [[Вратарь (футбол)|вратарь]]\n| имя =').replace('4 (1)<ref','4 (-54)<ref').replace('|4|1|0|0|0|0|0|0','|4|-54|0|0|0|0|0|0')


def lead_keeper_text():
    lead="'''Кальвин Браккельман''' — [[Германия|немецкий]] [[футбол]]ист, [[Вратарь (футбол)|вратарь]] клуба."
    return MINI.replace('Текст 4 (1)',lead).replace('4 (1)<ref','4 (-54)<ref')


def test_goalkeeper_without_card_position_can_update_signed_stats_from_explicit_lead():
    raw=lead_keeper_text()
    op=stats_operation();op['expected']['goals']=-54;op['new']['goals']=-57
    result=plan_article(snapshot(raw),package([op])['articles'][0],'1.1')
    assert result.changes[0].status=='ready'
    assert '5 (-57)<ref name="x"/><!-- статистика -->' in result.preview
    assert '| позиция =' not in result.preview


@pytest.mark.parametrize('case',['different_player','defender','stray_mention','later_paragraph'])
def test_other_goalkeeper_mentions_and_explicit_defender_do_not_change_role(case):
    if case=='different_player':raw=lead_keeper_text().replace("'''Кальвин Браккельман'''", "'''Другой футболист'''")
    elif case=='defender':raw=lead_keeper_text().replace('| имя =','| позиция = защитник\n| имя =')
    elif case=='stray_mention':raw=MINI.replace('Текст 4 (1)', 'Он играл рядом с [[Вратарь (футбол)|вратарём]]')
    else:raw=MINI+'\n\nКлуб подписал [[Вратарь (футбол)|вратаря]].'
    assert not is_goalkeeper(WikitextParser(raw),'Кальвин Браккельман')


def test_sofascore_position_disagreement_stops_signed_collection():
    from tests.test_sofascore import API,prepare
    class WrongRole(API):
        def details(self,pid):return {**super().details(pid),'position':'F'}
    with pytest.raises(UpdateError) as exc:prepare(lead_keeper_text(),api=WrongRole())
    assert exc.value.code=='goal_metric_mismatch'


@pytest.mark.parametrize('version', ['1.0','1.1'])
def test_signed_career_preserves_reference_and_text(version):
    op=stats_operation()
    op['expected']['goals']=-54
    op['new']['goals']=-57
    pkg=package([op]);pkg['schema_version']=version
    if version == '1.0': pkg.pop('is_test')
    validate_package(pkg)
    result=plan_article(snapshot(keeper_text()),pkg['articles'][0],version)
    assert result.changes[0].status=='ready'
    assert '5 (-57)<ref name="x"/><!-- статистика -->' in result.preview
    assert 'Текст 4 (1)' in result.preview
    assert plan_article(snapshot(result.preview),pkg['articles'][0],version).changes[0].status=='already_applied'


def test_negative_season_and_totals():
    op=operation('keeper-season','update_stats',entity={'name':'Аугсбург','wikitext':TABLE_AUGSBURG},
        target={'structure':'club_table'},season='2025/26',competition={'name':'Чемпионат','category':'Чемпионат','scope':'category'},
        expected={'appearances':4,'goals':-54},new={'appearances':5,'goals':-57})
    pkg=package([op]);validate_package(pkg)
    result=plan_article(snapshot(keeper_text()),pkg['articles'][0],'1.1')
    assert result.changes[0].status=='ready'
    assert '{{КлСтат/Сезон|2025/26|5|-57|' in result.preview
    assert '{{КлСтат/ИтогоЗаКлуб|Итого|5|-57|' in result.preview
    assert '{{КлСтат/Итого|5|-57|' in result.preview
    assert not arithmetic_warnings(result.preview)


def test_manual_negative_goals():
    page=snapshot(keeper_text())
    player,rows,_=article_rows(page)
    row=next(r for r in rows if r.goals==-54 and r.structure=='career')
    pkg=build_manual_package(page,row,5,-57,'https://example.org/source','2026-10-08')
    assert pkg['articles'][0]['operations'][0]['new']['goals']==-57
    assert any(r.goals==-54 and r.structure=='club_table' for r in rows)


@pytest.mark.parametrize('keeper,new',[(False,-1),(True,1)])
def test_metric_mismatch_rejected(keeper,new):
    op=stats_operation();op['new']['goals']=new
    pkg=package([op])
    result=plan_article(snapshot(keeper_text() if keeper else MINI),pkg['articles'][0],'1.1')
    assert result.changes[0].status=='goal_metric_mismatch'
    assert result.preview==result.snapshot.text


def dataset(conceded=57):
    row=player_row(5,0)
    stat=row['statistics'][0]
    stat['games']['position']='Goalkeeper'
    stat['goals']['conceded']=conceded
    return {'complete':True,'league':78,'season':2026,'fetched_at':utcnow(),'response':[row]}


def test_api_conceded_and_signed_anchor_delta():
    _,observations=normalize_dataset(dataset())
    obs=observations[0]
    assert obs['stats']=={'appearances':5,'goals':-57}
    assert obs['goal_metric']=='conceded'
    anchor=deepcopy(ANCHOR)
    anchor['career']={'appearances':14,'goals':-154}
    anchor['season_stats']={'appearances':4,'goals':-54}
    assert career_from_anchor(anchor,obs)=={'appearances':15,'goals':-157}
    op=update_operation(MAPPING,anchor,obs,anchor['career'])
    assert op['new']['goals']==-157


def test_missing_conceded_never_becomes_zero():
    _,observations=normalize_dataset(dataset(None))
    assert observations[0]['stats']['goals'] is None
    assert observations[0]['status']=='needs_review'
    anchor=deepcopy(ANCHOR);anchor['career']['goals']=-54;anchor['season_stats']['goals']=-54
    with pytest.raises(UpdateError): career_from_anchor(anchor,observations[0])


def test_scored_and_conceded_are_not_mixed():
    _,observations=normalize_dataset(dataset())
    with pytest.raises(UpdateError): career_from_anchor(ANCHOR,observations[0])
    assert not decreased({'appearances':5,'goals':-57},{'appearances':4,'goals':-54})
    assert decreased({'appearances':5,'goals':-50},{'appearances':4,'goals':-54})


def test_sofascore_requires_conceded_and_does_not_use_scored_zero():
    from wiki_stats.sofascore_cycle import _numbers
    response={'player_id':1,'tournament_id':35,'season_id':2,'type':'regularSeason',
              'team':{'id':3,'is_national':False,'sport':{'slug':'football'}},
              'statistics':{'appearances':10,'goals':0}}
    with pytest.raises(UpdateError): _numbers(response,1,35,2,'regularSeason',goalkeeper=True)
    response['statistics']['goals_conceded']=54
    assert _numbers(response,1,35,2,'regularSeason',goalkeeper=True)[1]['goals']==-54


def test_signed_numbers_for_new_season():
    from wiki_stats.klstat_editor import numbers
    from types import SimpleNamespace
    assert numbers(SimpleNamespace(categories=['Чемпионат']),{'complete':True,'categories':{'Чемпионат':{'appearances':10,'goals':-54}}})==[10,-54]
    with pytest.raises(UpdateError):
        numbers(SimpleNamespace(categories=['Чемпионат']),{'complete':True,'categories':{'Чемпионат':{'appearances':-10,'goals':-54}}})


def test_manual_correction_of_old_positive_goalkeeper_stat():
    raw=MINI.replace('| имя =','| позиция = вратарь\n| имя =')
    op=stats_operation();op['new']['goals']=-54
    pkg=package([op])
    result=plan_article(snapshot(raw),pkg['articles'][0],'1.1')
    assert result.changes[0].status=='ready'
    assert '5 (-54)<ref' in result.preview
