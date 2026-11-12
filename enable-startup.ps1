$ErrorActionPreference = 'Stop'
$launcher = Join-Path $PSScriptRoot 'background.vbs'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'AirPlayTray-Public.lnk'))
$shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
$shortcut.Arguments = '"' + $launcher + '"'
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'AirPlayTray public edition'
$shortcut.Save()
Write-Output 'Startup enabled. Remove AirPlayTray-Public.lnk from shell:startup to disable.'
