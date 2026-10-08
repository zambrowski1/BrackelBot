# SPDX-License-Identifier: MIT
import importlib.metadata as md
import json
import re
import sys
from pathlib import Path

root = Path(sys.argv[1])
scope = sys.argv[2]
entries = []
for d in sorted(md.distributions(), key=lambda d: d.metadata['Name'].lower()):
    name = d.metadata['Name']
    if name.lower() in {'pip', 'wikipedia-stats-updater'}:
        continue
    texts = []
    for item in d.files or []:
        path = Path(str(item))
        if '.dist-info' not in str(path) or not re.search(r'(licen[sc]e|copying|notice|authors)', path.name, re.I):
            continue
        source = Path(d.locate_file(item))
        if not source.is_file():
            continue
        target = root / 'third_party' / scope / name / path.relative_to(path.parts[0])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
        texts.append(target.relative_to(root).as_posix())
    entries.append({'name': name, 'version': d.version, 'license': d.metadata.get('License-Expression') or d.metadata.get('License'), 'license_classifiers': [c for c in d.metadata.get_all('Classifier', []) if c.startswith('License')], 'project_urls': d.metadata.get_all('Project-URL', []), 'license_files': texts})
out = root / 'docs' / ('dependency-licenses-' + scope + '.json')
out.write_text(json.dumps(entries, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
for entry in entries:
    print(entry['name'], entry['version'], str(entry['license'])[:140], 'files=' + str(len(entry['license_files'])))
