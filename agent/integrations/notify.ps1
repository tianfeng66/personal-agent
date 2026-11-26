$ErrorActionPreference = 'Stop'
Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing
$icon = New-Object System.Windows.Forms.NotifyIcon
try {
    $icon.Icon = [System.Drawing.SystemIcons]::Information
    $icon.Visible = $true
    $icon.ShowBalloonTip(5000, $env:AGENT_NOTICE_TITLE, $env:AGENT_NOTICE_TEXT, 'Info')
    Start-Sleep -Seconds 6
} finally {
    $icon.Dispose()
}
