param([string]$SourceDirectory, [string]$Mode)
$ErrorActionPreference = 'Stop'
$global:StoppedIds = @()
function Get-CimInstance {
    param($ClassName, $Filter)
    if ($Mode -eq 'enumeration_failure') { throw 'Simulated access denied' }
    @(
        [pscustomobject]@{ ProcessId=900001; CommandLine=('pythonw.exe "' + (Join-Path $SourceDirectory 'tray_app.py') + '"') },
        [pscustomobject]@{ ProcessId=900002; CommandLine=('python.exe "' + (Join-Path $SourceDirectory 'airplay_system_audio.py') + '" --debug') },
        [pscustomobject]@{ ProcessId=900003; CommandLine=('pythonw.exe "' + (Join-Path ($SourceDirectory + '-other') 'tray_app.py') + '"') },
        [pscustomobject]@{ ProcessId=900004; CommandLine=('python.exe "' + (Join-Path $SourceDirectory 'tray_app.py.bak') + '"') },
        [pscustomobject]@{ ProcessId=900005; CommandLine=$null }
    )
}
function Stop-Process {
    param($Id, [switch]$Force, $ErrorAction)
    if ($Id -notin 900001,900002) { throw 'Test attempted to stop an unrelated process' }
    if ($Mode -eq 'stop_failure') { throw 'Simulated stop failure' }
    $global:StoppedIds += $Id
}
function Remove-Item { throw 'No PID file should be touched in this test' }
& (Join-Path $SourceDirectory 'stop_background.ps1')
$result = $LASTEXITCODE
if ($Mode -eq 'success') {
    if ($result -ne 0 -or ($global:StoppedIds -join ',') -ne '900001,900002') { throw 'Scoped stop failed' }
} elseif ($result -ne 1) { throw 'Failure was reported as success' }
Write-Output ('PASS ' + $Mode + ': no real process termination executed')
exit 0
