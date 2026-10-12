"""Render recorded bot outputs through read-only Wikipedia parse and package them."""
import hashlib,html,json,re,sys
from pathlib import Path
from zipfile import ZipFile,ZIP_DEFLATED
import requests

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/'handoff/2026-10-13/five-bundesliga-review'
NOTES={
 'Кобель':'Обновлены карточки клуба и сборной; создана клубная таблица с сохранением неизвестных показателей.',
 'Кариус':'Обновлены карточка и клубная таблица. Блок Шальке разделён по лигам: Вторая Бундеслига и Бундеслига. В карточке учтены четыре матча первого сезона после январского перехода: прежний вывод 35/−30 был неполным; новый — 39/−35.',
 'Раум':'Создана клубная таблица; существующая таблица голов за сборную сохранена. Основная сборная обновлена до 40/1 с датой 04.10.2026. Молодёжные и олимпийская строки сохранены; новый СбМатчи не добавлен.',
 'Орбан':'Обновлена карточка, преобразована обычная клубная таблица и добавлены сезоны. Карточка сборной осталась прежней.',
 'Штиллер':'Обновлены карточки клуба и сборной, заменён старый шаблон карьеры, добавлен сезон. Сборная в карточке — по агрегату; недостающие подробные матчи не создавались. Ответ по Лиге чемпионов 2024/25 недоступен.'}
CSS='body{font:16px/1.6 Arial,sans-serif;max-width:1200px;margin:30px auto;padding:0 25px;color:#202122}a{color:#36c}table{border-collapse:collapse;font-size:90%}td,th{padding:4px 7px}table.wikitable td,table.wikitable th{border:1px solid #a2a9b1}.wikitable{background:#f8f9fa;margin:1em 0}.infobox{float:right;max-width:340px;width:300px;background:#f8f9fa;border:1px solid #a2a9b1;margin:0 0 20px 24px;padding:7px;font-size:90%}.references{font-size:85%}.mw-editsection{display:none}.preview-note{padding:15px;background:#fff4d5;border:1px solid #d8a138;margin-bottom:20px}.mw-parser-output{overflow-wrap:anywhere}h2{border-bottom:1px solid #a2a9b1}img{max-width:100%;height:auto}'


def main():
    report=json.loads((OUT/'report.json').read_text(encoding='utf8'));sections=[];manifest=[]
    md=['# Пять статей для просмотра','','Текущие статьи получены 13 октября 2026 года. Кляйндинст и Браккельман исключены. Вывод бота не исправлялся вручную; Википедия не редактировалась.','',
        'Откройте index.html: обычный вид статей, исходники, новые тексты и diff. Предпросмотр получен через read-only действие parse API Википедии.','',
        '| Игрок | Текущий клуб: до → после | Дата после | Что произошло |','|---|---|---|---|']
    session=requests.Session();session.headers['User-Agent']='BrackelBot/0.4 (read-only previews; Wikipedia user Zambrowski)'
    for article in report['articles']:
        name=article['player'];after=OUT/(name+'-после.wiki');text=after.read_text(encoding='utf8')
        digest=hashlib.sha256(after.read_bytes()).hexdigest();saved=OUT/(name+'-rendered.json')
        data=json.loads(saved.read_text(encoding='utf8')) if saved.exists() else {}
        if data.get('source_sha256')!=digest:
            response=session.post('https://ru.wikipedia.org/w/api.php',data={'action':'parse','format':'json','formatversion':2,'text':text,'title':article['title'],'contentmodel':'wikitext','prop':'text','disableeditsection':1},timeout=30)
            response.raise_for_status();data=response.json()
            if 'parse' not in data:raise RuntimeError('No confirmed Wikipedia preview for '+name)
            data['source_sha256']=digest;saved.write_text(json.dumps(data,ensure_ascii=False),encoding='utf8')
        content=data['parse']['text']
        content=re.sub(r'(src|href)="//',r'\1="https://',content)
        content=re.sub(r'(src|href)="/(?!/)',r'\1="https://ru.wikipedia.org/',content)
        errors=re.findall(r'<(?:strong|span)[^>]*class="[^"]*(?:error|scribunto-error)[^"]*"[^>]*>(.*?)</(?:strong|span)>',content,re.S)
        article['render_errors']=[re.sub('<[^>]+>','',v) for v in errors];article['render_source_sha256']=digest
        title=html.escape(article['title'])
        notice='<div class="preview-note">Вывод бота для просмотра. Не опубликован. <a href="'+name+'-после.wiki">Викитекст</a> · <a href="'+name+'-изменения.diff">Изменения</a></div>'
        (OUT/(name+'-просмотр.html')).write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>'+title+'</title><style>'+CSS+'</style><body>'+notice+'<h1>'+title+'</h1>'+content+'</body></html>',encoding='utf8')
        old=article['before_card']['клубы'][-1][-1];new=article['after_card']['клубы'][-1][-1];day=article['after_card'].get('обновление данных о клубе','')
        md.append('| '+name+' | '+old+' → '+new+' | '+day+' | '+NOTES[name]+' |')
        links=' · '.join('<a href="'+name+suffix+'">'+label+'</a>' for suffix,label in [('-просмотр.html','Посмотреть статью'),('-после.wiki','Новый викитекст'),('-до.wiki','Исходник'),('-изменения.diff','Diff')])
        sections.append('<section><h2>'+title+'</h2><p><b>'+html.escape(old+' → '+new)+'</b>; дата: '+day+'</p><p>'+html.escape(NOTES[name])+'</p><p>'+links+'</p><small><a href="https://ru.wikipedia.org/w/index.php?oldid='+str(article['revision'])+'">Исходная ревизия '+str(article['revision'])+'</a></small></section>')
        for suffix in ['-до.wiki','-после.wiki','-изменения.diff','-просмотр.html']:
            path=OUT/(name+suffix);manifest.append({'file':path.name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()})
        print(name,'render errors:',article['render_errors'],flush=True)
    md+=['','Все пять: повторный запуск без изменений, сноски сохранены, новых арифметических ошибок нет. Это не независимая сверка каждого числа с другим поставщиком.','',
         'Для Штиллера ранее потрачено 38 запросов Sofascore; осталось 237. Исправления и повторные прогоны новых запросов не расходуют. Неизвестные числа сохраняются.','',
         'Повторить без футбольного API: `python -X utf8 tools/review_five_bundesliga.py`. Обновить обычный предпросмотр: `python -X utf8 tools/render_five_review.py` — только чтение Wikimedia. Причины пропусков: report.json.']
    (OUT/'Открой_сначала.md').write_text('\n'.join(md),encoding='utf8')
    (OUT/'index.html').write_text('<!doctype html><html lang="ru"><meta charset="utf-8"><title>Пять статей — вывод BrackelBot</title><style>body{font:16px/1.5 Arial;max-width:1000px;margin:30px auto;padding:20px;background:#fafafa}section{background:white;border:1px solid #ccc;border-radius:8px;padding:18px;margin:20px 0}a{color:#36c}</style><body><h1>Пять статей — вывод BrackelBot</h1><p>Результаты после исправления логики бота. Без ручного редактирования и публикации в Википедию.</p>'+''.join(sections)+'<p>Исходные статьи — Википедия, CC BY-SA; ссылки на ревизии приведены выше. Sofascore: осталось 237 запросов; новые прогоны используют кеш.</p></body></html>',encoding='utf8')
    (OUT/'report.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf8')
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2),encoding='utf8')
    with ZipFile(OUT/'five-articles.zip','w',ZIP_DEFLATED) as archive:
        for path in sorted(OUT.iterdir()):
            if path.suffix in {'.wiki','.diff','.md','.html'} or path.name in {'report.json','collection.json','manifest.json','tests.txt'}:archive.write(path,path.name)
    with ZipFile(OUT/'five-articles.zip') as archive:
        assert archive.testzip() is None
        for item in manifest:assert hashlib.sha256(archive.read(item['file'])).hexdigest()==item['sha256']
    return 0 if all(not a['render_errors'] for a in report['articles']) else 1


if __name__=='__main__':sys.exit(main())
