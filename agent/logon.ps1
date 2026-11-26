param([ValidateSet('install','uninstall','status')]$Operation)
$ErrorActionPreference = 'Stop'
$user = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$name = $env:PA_TASK + '-' + [Security.Principal.WindowsIdentity]::GetCurrent().User.Value
$existing = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
if ($Operation -eq 'status') {
    if ($existing) { Write-Output "Logon task: $($existing.State)" } else { Write-Output 'Logon task: not installed' }
    exit 0
}
if ($Operation -eq 'install') {
    $listeners = [Net.NetworkInformation.IPGlobalProperties]::GetIPGlobalProperties().GetActiveTcpListeners()
    if ((!$existing -or $existing.State -ne 'Running') -and ($listeners | Where-Object Port -eq ([int]$env:PA_PORT))) {
        throw 'Port in use. Close the foreground server before enabling automatic startup.'
    }
}
if ($existing -and $existing.State -eq 'Running') {
    Stop-ScheduledTask -TaskName $name
    for ($i=0; $i -lt 30 -and (Get-ScheduledTask -TaskName $name).State -eq 'Running'; $i++) {
        Start-Sleep -Milliseconds 200
    }
}
if ($Operation -eq 'uninstall') {
    if ($existing) { Unregister-ScheduledTask -TaskName $name -Confirm:$false }
    Write-Output 'Logon task removed.'
    exit 0
}
$action = New-ScheduledTaskAction -Execute $env:PA_EXE -WorkingDirectory $env:PA_ROOT -Argument "-m agent.service_windows background $env:PA_PORT"
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $user
$principal = New-ScheduledTaskPrincipal -UserId $user -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -ExecutionTimeLimit ([TimeSpan]::Zero)
Register-ScheduledTask -TaskName $name -Trigger $trigger -Action $action -Settings $settings -Principal $principal -Force | Out-Null
Start-ScheduledTask -TaskName $name
