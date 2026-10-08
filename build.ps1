$ErrorActionPreference = 'Stop'
Set-Location -LiteralPath $PSScriptRoot
python -m PyInstaller --noconfirm --clean WikipediaStatsUpdater.spec
if ($LASTEXITCODE -ne 0) { throw 'Сборка PyInstaller завершилась с ошибкой' }
