# One-time elevated setup for Architecture B.
#
# Windows Firewall blocks inbound connections by default, so the ESP32 cannot
# reach the broker on this machine until port 1884 is opened. Everything else
# in Architecture B works without admin -- this is the only elevated step.
#
# Run ONCE in an Administrator PowerShell:
#     powershell -ExecutionPolicy Bypass -File setup_firewall.ps1
#
# To undo:
#     Remove-NetFirewallRule -DisplayName "POC MQTT Telemetry Broker (1884)"

$ErrorActionPreference = "Stop"
$RuleName = "POC MQTT Telemetry Broker (1884)"
$Port = 1884

$id = [Security.Principal.WindowsIdentity]::GetCurrent()
if (-not (New-Object Security.Principal.WindowsPrincipal($id)).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)) {
    Write-Error "Must be run from an Administrator PowerShell."
    exit 1
}

if (Get-NetFirewallRule -DisplayName $RuleName -ErrorAction SilentlyContinue) {
    Write-Output "Firewall rule already present: $RuleName"
} else {
    # All profiles: a phone hotspot usually registers as Public, and the rule
    # must survive the laptop moving between networks.
    New-NetFirewallRule -DisplayName $RuleName `
        -Direction Inbound -Action Allow -Protocol TCP -LocalPort $Port `
        -Profile Any | Out-Null
    Write-Output "Created firewall rule '$RuleName' allowing inbound TCP $Port."
}

Write-Output ""
Write-Output "LAN addresses the ESP32 can use for MQTT_BROKER_HOST:"
Get-NetIPAddress -AddressFamily IPv4 |
    Where-Object { $_.IPAddress -notlike '127.*' -and $_.IPAddress -notlike '169.254.*' } |
    Select-Object IPAddress, InterfaceAlias |
    Format-Table -AutoSize

Write-Output "Put the address matching the network the ESP32 joins into"
Write-Output "firmware/esp32_generic_functions/include/telemetry_config.h"
