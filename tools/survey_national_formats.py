"""Recheck saved article revisions without football API calls or wiki edits.

Run from the repository root:
python tools/survey_national_formats.py --snapshots PATH --output PATH
PATH must contain survey.json and its numbered text_file snapshots.
"""
import argparse,json,sys
from pathlib import Path
from collections import Counter
import mwparserfromhell as mw
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from wiki_stats.national_table_formats import classify
from wiki_stats.national_table_migration import operation,migrate
from wiki_stats.errors import UpdateError


def main():
    cli=argparse.ArgumentParser(description=__doc__)
    cli.add_argument('--snapshots',required=True,type=Path)
    cli.add_argument('--output',required=True,type=Path)
    args=cli.parse_args()
    records=json.loads((args.snapshots/'survey.json').read_text(encoding='utf8'))
    for row in records:
        text=(args.snapshots/row['text_file']).read_text(encoding='utf8')
        counts=Counter()
        for table in mw.parse(text).filter_tags():
            if str(table.tag).casefold()=='table':
                kind=classify(table)
                if kind!='unknown':counts[kind]+=1
        row['recognized_formats']=dict(counts)
        row.pop('migration_code',None)
        try:
            if not row['player']:
                row['migration_status']='Нужно подтвердить имя: в карточке нет параметра «имя»'
            else:
                op=operation(text,row['player'],'2026-10-12')
                if op:
                    migrate(text,row['player'],op)
                    row['migration_status']='Перенос проверен: '+str(op['payload']['match_count'])+' матчей'
                else:row['migration_status']='Нет распознаваемого старого списка матчей'
        except UpdateError as error:
            row['migration_status']=str(error);row['migration_code']=error.code
    fields=('title','revid','source','has_klstat','has_sbmatches','recognized_formats','migration_status')
    args.output.write_text(json.dumps([{k:r[k] for k in fields} for r in records],ensure_ascii=False,indent=2)+'\n',encoding='utf8')
    print('Checked:',len(records),'migrations:',sum(r['migration_status'].startswith('Перенос проверен') for r in records))


if __name__=='__main__':main()
