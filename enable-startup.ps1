$ErrorActionPreference = 'Stop'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath('Startup')) 'AirPlayTray-Public.lnk'))
$exe = Join-Path $PSScriptRoot 'AirPlayTray.exe'
if (Test-Path $exe) {
    $shortcut.TargetPath = $exe
    $shortcut.Arguments = ''
    $shortcut.IconLocation = "$exe,0"
} else {
    $launcher = Join-Path $PSScriptRoot 'background.vbs'
    $shortcut.TargetPath = Join-Path $env:SystemRoot 'System32\wscript.exe'
    $shortcut.Arguments = '"' + $launcher + '"'
}
$shortcut.WorkingDirectory = $PSScriptRoot
$shortcut.WindowStyle = 7
$shortcut.Description = 'AirPlayTray public edition'
$shortcut.Save()
Write-Output 'Startup enabled. Remove AirPlayTray-Public.lnk from shell:startup to disable.'
