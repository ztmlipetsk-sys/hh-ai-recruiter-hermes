@echo off
setlocal DisableDelayedExpansion
chcp 65001 >nul
title Hermes - восстановление ярлыка
echo Восстановление ярлыка существующего Hermes.
echo.
"%SystemRoot%\System32\WindowsPowerShell\v1.0\powershell.exe" -NoLogo -Command ^
 "$ErrorActionPreference = 'Stop';" ^
 "try {" ^
 "  $hermes = Get-Command hermes -ErrorAction Stop;" ^
 "  $desktop = [Environment]::GetFolderPath('Desktop');" ^
 "  if (-not $desktop -or -not (Test-Path -LiteralPath $desktop -PathType Container)) { throw 'Не найдена папка рабочего стола.' };" ^
 "  $target = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe';" ^
 "  $single = [string][char]39;" ^
 "  $launch = 'hermes -p default chat';" ^
 "  if ($hermes.CommandType -eq 'Application') { $escaped = $hermes.Source.Replace($single, $single + $single); $launch = '& ' + $single + $escaped + $single + ' -p default chat' };" ^
 "  if ($env:HERMES_HOME) { $escapedHome = $env:HERMES_HOME.Replace($single, $single + $single); $launch = '$env:HERMES_HOME = ' + $single + $escapedHome + $single + '; ' + $launch };" ^
 "  $link = Join-Path $desktop 'Hermes.lnk';" ^
 "  if (Test-Path -LiteralPath $link) { $backupDir = Join-Path ([Environment]::GetFolderPath('LocalApplicationData')) 'HermesShortcutBackup'; New-Item -ItemType Directory -Path $backupDir -Force | Out-Null; $backup = Join-Path $backupDir ('Hermes-' + [guid]::NewGuid().ToString('N') + '.lnk'); Copy-Item -LiteralPath $link -Destination $backup; Write-Host ('Сохранена копия прежнего ярлыка: ' + $backup) };" ^
 "  $shell = New-Object -ComObject WScript.Shell;" ^
 "  $shortcut = $shell.CreateShortcut($link);" ^
 "  $shortcut.TargetPath = $target;" ^
 "  $shortcut.Arguments = '-NoLogo -NoExit -Command ' + [char]34 + $launch + [char]34;" ^
 "  $workingDir = Join-Path ([Environment]::GetFolderPath('UserProfile')) 'HermesSetup';" ^
 "  if (-not (Test-Path -LiteralPath $workingDir -PathType Container)) { $workingDir = [Environment]::GetFolderPath('UserProfile') };" ^
 "  $shortcut.WorkingDirectory = $workingDir;" ^
 "  $shortcut.Description = 'Hermes: существующий профиль default';" ^
 "  $shortcut.IconLocation = $target + ',0';" ^
 "  $shortcut.WindowStyle = 1;" ^
 "  $shortcut.Save();" ^
 "  if (-not (Test-Path -LiteralPath $link -PathType Leaf)) { throw 'Windows не сохранила ярлык.' };" ^
 "  $saved = $shell.CreateShortcut($link);" ^
 "  if ($saved.TargetPath -ine $target -or $saved.Arguments -cne $shortcut.Arguments) { throw 'Содержимое сохранённого ярлыка не совпало с заданным.' };" ^
 "  Write-Host ''; Write-Host ('ГОТОВО: ярлык создан — ' + $link) -ForegroundColor Green;" ^
 "  Write-Host 'Дважды щёлкните по ярлыку Hermes на рабочем столе.';" ^
 "} catch { Write-Host ('ОШИБКА: ' + $_.Exception.Message) -ForegroundColor Red; Write-Host 'Пришлите снимок этого окна. Если hermes не найден, выполните файл из PowerShell, где команда hermes работает.'; exit 1 };" ^
 "Write-Host ''; Write-Host 'Установленные профили:';" ^
 "try { & hermes profile list; Write-Host ''; Write-Host 'Состояние существующего шлюза:'; & hermes -p default gateway status; Write-Host ''; Write-Host 'running — запущен; stopped — остановлен. Ответ бота проверьте сообщением в Telegram.' } catch { Write-Host ('Ярлык создан; проверка шлюза не завершилась: ' + $_.Exception.Message) -ForegroundColor Yellow };" ^
 "exit 0"
set "HERMES_SHORTCUT_EXIT=%ERRORLEVEL%"
echo.
echo Нажмите любую клавишу, чтобы закрыть окно.
pause >nul
exit /b %HERMES_SHORTCUT_EXIT%
