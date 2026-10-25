param([Parameter(Mandatory = $true)][string]$School)

$ErrorActionPreference = "Stop"
$dataRoot = Join-Path $env:LOCALAPPDATA "CampusOpportunityWatcher"

Get-ScheduledTask -TaskName "校园机会提醒-$School-*" -ErrorAction SilentlyContinue |
    Unregister-ScheduledTask -Confirm:$false
Remove-Item -LiteralPath (Join-Path ([Environment]::GetFolderPath("Desktop")) "校园机会提醒-$School.lnk") -ErrorAction SilentlyContinue

# 没有其他学校的计划任务时才删除程序本体。
if (-not (Get-ScheduledTask -TaskName "校园机会提醒-*" -ErrorAction SilentlyContinue)) {
    $exe = Join-Path $dataRoot "CampusOpportunityWatcher.exe"
    if (Test-Path -LiteralPath $exe) {
        try { Remove-Item -LiteralPath $exe -Force } catch { throw "无法删除程序文件，请先关闭校园机会提醒后重试。" }
    }
}
Write-Host "已移除 $School 的计划任务和快捷方式。档案、历史和日志保留在 $dataRoot\$School。"
