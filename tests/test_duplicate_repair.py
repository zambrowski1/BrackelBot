# SPDX-License-Identifier: MIT
from copy import deepcopy
import pytest
from wiki_stats.structural_editor import execute_operation
from wiki_stats.change_planner import plan_article
from wiki_stats.schema_validator import validate_package
from wiki_stats.errors import UpdateError
from tests.stage23_helpers import MINI,AUGSBURG,operation,snapshot
from tests.test_sofascore import prepare,API,Resolver


def career_op():
 return operation('dedupe','repair_career_duplicates',entity={'name':'Аугсбург','wikitext':AUGSBURG},
  target={'structure':'career','field':'клубы','period':'2026—{{н.в.}}'},
  payload={'expected_stats':[{'appearances':4,'goals':1},{'appearances':8,'goals':2}],
           'stats':{'appearances':9,'goals':3}})


def duplicated_card():
 return MINI.replace('|2026—{{н.в.}}|'+AUGSBURG+'| 4 (1)<ref name="x"/><!-- статистика -->',
  '|2026—{{н.в.}}|'+AUGSBURG+'| 4 (1)<ref name="x"/><!-- статистика -->\n|2026—{{н.в.}}|'+AUGSBURG+'|8 (2)')


def test_card_repair_preserves_first_reference_and_other_text_and_replays():
 raw=duplicated_card();op=career_op()
 fixed,status=execute_operation(raw,'Кальвин Браккельман',op)
 assert status=='ready' and '9 (3)<ref name="x"/>' in fixed
 assert fixed.count('|2026—{{н.в.}}|')==1
 assert execute_operation(fixed,'Кальвин Браккельман',op)[1]=='already_applied'
 assert fixed[fixed.index('{{КлСтат|'):]==raw[raw.index('{{КлСтат|'):]


@pytest.mark.parametrize('note',['<ref>не терять</ref>','<!-- не терять -->'])
def test_card_duplicate_with_note_requires_review(note):
 with pytest.raises(UpdateError,match='сноска или комментарий'):
  execute_operation(duplicated_card().replace('8 (2)','8 (2)'+note),'Кальвин Браккельман',career_op())


def table_raw():
 return '{{Футболист|имя=Тест}}\nДо<!-- оставить -->\n{{КлСтат|Чемпионат|Национальный кубок}}\n'+\
  '{{КлСтат/Клуб|Клуб|сезоны=2}}\n{{КлСтат/Лига|Лига|сезоны=2}}\n'+\
  '{{КлСтат/Сезон|2025/26|12|1|0|0}}\n{{КлСтат/Сезон|2025/26|13|2|0|0}}\n'+\
  '{{КлСтат/ИтогоЗаКлуб|Итого|25|3|0|0}}\n{{КлСтат/Итого|25|3|0|0}}\nПосле'


def table_op():
 return operation('dedupe','repair_duplicate_season',entity={'name':'Клуб','wikitext':'Клуб'},
  target={'structure':'club_table'},season='2025/26',payload={'expected_rows':[
   {'Чемпионат':{'appearances':12,'goals':1},'Национальный кубок':{'appearances':0,'goals':0}},
   {'Чемпионат':{'appearances':13,'goals':2},'Национальный кубок':{'appearances':0,'goals':0}}],
   'categories':{'Чемпионат':{'appearances':30,'goals':3}}})


def test_table_duplicate_repair_counts_totals_and_idempotence():
 raw=table_raw();op=table_op();fixed,status=execute_operation(raw,'Тест',op)
 assert status=='ready' and fixed.count('КлСтат/Сезон')==1
 assert fixed.count('сезоны=1')==2
 assert '{{КлСтат/Итого|30|3|0|0}}' in fixed
 assert 'До<!-- оставить -->' in fixed and fixed.endswith('После')
 assert execute_operation(fixed,'Тест',op)[1]=='already_applied'


def test_conflicting_unknown_cells_and_changed_expected_rows_refuse():
 raw=table_raw().replace('|13|2|0|0}}','|13|2|1|0}}');op=table_op()
 op['payload']['expected_rows'][1]['Национальный кубок']['appearances']=1
 with pytest.raises(UpdateError,match='неподтверждённые'):
  execute_operation(raw,'Тест',op)
 with pytest.raises(UpdateError,match='Изменились'):
  execute_operation(raw,'Тест',table_op())


def test_pipeline_repairs_duplicate_seasons_before_numerical_updates():
 text=MINI.replace('сезоны=1','сезоны=2').replace('{{КлСтат/Сезон|2025/26|4|1|0|0|0|0|0|0}}',
  '{{КлСтат/Сезон|2025/26|4|1|0|0|0|0|0|0}}\n{{КлСтат/Сезон|2025/26|8|2|0|0|0|0|0|0}}')
 pkg,plan,report=prepare(text)
 validate_package(pkg)
 assert any(c.operation['type']=='repair_duplicate_season' and c.status=='ready' for c in plan.changes)
 assert plan.preview.count('КлСтат/Сезон')==1
 assert all(c.status in ('ready','already_applied') for c in plan.changes)
 assert not plan.publishable and any('не подтверждены' in w for w in plan.warnings)
 replay=plan_article(snapshot(plan.preview),pkg['articles'][0],'1.1',Resolver())
 assert replay.preview==plan.preview and not replay.diff

def test_pipeline_repairs_duplicate_career_and_wrong_current_club():
 from tests.stage23_helpers import WERDER
 raw=duplicated_card().replace('| нынешний клуб = '+AUGSBURG,'| нынешний клуб = '+WERDER)
 pkg,plan,report=prepare(raw)
 validate_package(pkg)
 assert any(c.operation['type']=='repair_career_duplicates' and c.status=='ready' for c in plan.changes)
 assert '| нынешний клуб = '+AUGSBURG in plan.preview
 assert plan.preview.count('|2026—{{н.в.}}|')==1
 assert all(c.status in ('ready','already_applied') for c in plan.changes)
 assert plan_article(snapshot(plan.preview),pkg['articles'][0],'1.1',Resolver()).preview==plan.preview

def test_current_club_accepts_verified_link_with_different_display():
 from tests.stage23_helpers import FakeResolver,WERDER
 class Wiki:
  def get_page_identity(self,title):
   return {'qid':'Q15755' if title=='Аугсбург (футбольный клуб)' else 'Q999'}
 resolver=FakeResolver();resolver.client=Wiki()
 raw=MINI.replace('| нынешний клуб = '+AUGSBURG,'| нынешний клуб = '+WERDER)
 raw=raw.replace('|'+AUGSBURG+'|','|[[Аугсбург (футбольный клуб)|Аугсбург другой текст]]|')
 op=operation('current','update_current_club',entity={'name':'Аугсбург','kind':'club','wikidata_id':'Q15755','wikitext':AUGSBURG},
  target={'structure':'career','field':'клубы'},expected=WERDER,payload={})
 fixed,status=execute_operation(raw,'Кальвин Браккельман',op,resolver)
 assert status=='ready' and '| нынешний клуб = '+AUGSBURG in fixed
 assert 'Аугсбург другой текст' in fixed
 with pytest.raises(UpdateError):execute_operation(raw.replace('Аугсбург (футбольный клуб)|Аугсбург другой текст','Вердер (футбольный клуб)|Аугсбург другой текст'),'Кальвин Браккельман',op,resolver)
