# Finds (and optionally kills) whatever process is holding a Windows COM
# port open, so pyserial-based tools and PlatformIO can use it.
#
# Multiple entry points in this project compete for the SAME physical COM
# port -- Windows only allows one process to hold it at a time:
#   pio device monitor / pio run -t upload   (firmware flashing/monitoring)
#   agent/server.py                          (dashboard backend)
#   agent/agent_loop.py                      (discovery CLI)
#   agent/composition_task.py                (composition CLI)
#   agent/briefing_task.py                   (briefing CLI)
#   skills/skill_runner.py                   (deterministic skill replay)
#
# Whichever of these you ran most recently and left running blocks the next
# one with: serial.serialutil.SerialException: could not open port 'COMx':
# PermissionError(13, 'Access is denied.')
#
# This has hit real users of this project at least twice with two DIFFERENT
# culprits (the PlatformIO monitor once, the dashboard another time) -- so
# rather than one-off "close X first" instructions per step, this script is
# the single canonical fix: run it whenever you hit that error, regardless
# of which prior step caused it.
#
# Usage:
#   .\free_com_port.ps1                 # report only, uses SERIAL_PORT from .env or COM6
#   .\free_com_port.ps1 -Port COM6      # report only, explicit port
#   .\free_com_port.ps1 -Kill           # actually terminate the blocking process(es)
#   .\free_com_port.ps1 -Port COM6 -Kill

param(
    [string]$Port,
    [switch]$Kill
)

if (-not $Port) {
    $envFile = Join-Path $PSScriptRoot ".env"
    if (Test-Path $envFile) {
        $line = Get-Content $envFile | Where-Object { $_ -match '^SERIAL_PORT=' } | Select-Object -First 1
        if ($line) { $Port = ($line -split '=', 2)[1].Trim() }
    }
    if (-not $Port) { $Port = "COM6" }
}

Write-Output "Target port: $Port"
Write-Output "Checking for known processes that hold a serial port open..."

$pattern = 'device monitor|agent[\\/]server\.py|agent[\\/]agent_loop\.py|agent[\\/]composition_task\.py|agent[\\/]briefing_task\.py|skills[\\/]skill_runner\.py'
$candidates = Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match $pattern }

if (-not $candidates) {
    Write-Output "No known port-holding process found. $Port should be free."
    Write-Output "If it's still denied, something outside this project has it open -- check Device Manager > Ports, or another terminal/IDE."
    exit 0
}

Write-Output ""
Write-Output "Found $($candidates.Count) process(es) that may be holding $Port open:"
$candidates | Select-Object ProcessId, Name, CommandLine | Format-Table -AutoSize -Wrap

if ($Kill) {
    foreach ($p in $candidates) {
        Write-Output "Stopping PID $($p.ProcessId) ($($p.Name))..."
        Stop-Process -Id $p.ProcessId -Force -ErrorAction SilentlyContinue
    }
    Start-Sleep -Seconds 1
    Write-Output "Done. $Port should now be free -- retry your command."
} else {
    Write-Output ""
    Write-Output "Re-run with -Kill to stop these processes:"
    Write-Output "  .\free_com_port.ps1 -Port $Port -Kill"
}
