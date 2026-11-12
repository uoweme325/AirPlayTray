$ErrorActionPreference = 'Stop'
$projectDirectory = [IO.Path]::GetFullPath($PSScriptRoot)
$pidFile = Join-Path $projectDirectory 'airplay-system-audio.pid'
$scriptPaths = @(
    (Join-Path $projectDirectory 'tray_app.py'),
    (Join-Path $projectDirectory 'airplay_system_audio.py')
)
$scriptPattern = '(?i)(?:^|[\s"])(?:' + (($scriptPaths | ForEach-Object { [regex]::Escape($_) }) -join '|') + ')(?=[\s"]|$)'

try {
    # Verify the script path even when a PID file exists: Windows can reuse old PIDs.
    $processes = @(Get-CimInstance Win32_Process -Filter "Name='pythonw.exe' OR Name='python.exe'" |
        Where-Object { $_.CommandLine -match $scriptPattern })
    foreach ($process in $processes) {
        Stop-Process -Id $process.ProcessId -Force -ErrorAction Stop
    }
    if (Test-Path -LiteralPath $pidFile) {
        Remove-Item -LiteralPath $pidFile -Force
    }
    exit 0
} catch {
    Write-Error ("Could not verify or stop AirPlay processes: " + $_.Exception.Message) -ErrorAction Continue
    exit 1
}
