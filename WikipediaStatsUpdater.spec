# -*- mode: python ; coding: utf-8 -*-
from pathlib import Path
from PyInstaller.utils.hooks import collect_data_files

project = Path(SPECPATH)
datas = collect_data_files('wiki_stats') + collect_data_files('jsonschema_specifications')
a = Analysis(
    [str(project / 'run_gui.py')], pathex=[str(project)], binaries=[], datas=datas,
    hiddenimports=[], hookspath=[], hooksconfig={}, runtime_hooks=[], excludes=[],
    noarchive=False, optimize=0,
)
# Qt's unversioned ICU imports use Windows' system ICU. DLLs with the same
# basename from Poppler/Conda expose version-suffixed symbols instead and
# must not shadow Windows ICU in the frozen application's DLL directory.
# Leave ICU shipped inside an actual Qt wheel intact for other Qt versions.
a.binaries = [entry for entry in a.binaries
              if not (Path(entry[0]).name.lower() in {'icuuc.dll', 'icuin.dll'}
                      and not any(part.lower() == 'pyside6' for part in Path(entry[1]).parts))
              and not (Path(entry[0]).name.lower().startswith('icudt')
                       and 'poppler' in entry[1].lower())]
pyz = PYZ(a.pure)
exe = EXE(pyz, a.scripts, [], exclude_binaries=True, name='WikipediaStatsUpdater',
          debug=False, bootloader_ignore_signals=False, strip=False, upx=False,
          console=False, disable_windowed_traceback=False)
coll = COLLECT(exe, a.binaries, a.datas, strip=False, upx=False, name='WikipediaStatsUpdater')
