param(
    [Parameter(Mandatory = $true)][string]$School,
    [switch]$FromSource,
    [string]$FirstTime = "08:00",
    [string]$SecondTime = "20:00"
)

$ErrorActionPreference = "Stop"
$dataRoot = Join-Path $env:LOCALAPPDATA "CampusOpportunityWatcher"
New-Item -ItemType Directory -Path $dataRoot -Force | Out-Null

if ($FromSource) {
    $repo = Split-Path $PSScriptRoot -Parent
    $python = (Get-Command pythonw.exe -ErrorAction SilentlyContinue).Source
    if (-not $python) { throw "找不到 pythonw.exe，请先安装 Python 3.11+ 并加入 PATH。" }
    $program = $python
    $prefix = "`"$(Join-Path $repo 'run_watcher.py')`" "
    $workDir = $repo
} else {
    $exeSource = Join-Path $PSScriptRoot "CampusOpportunityWatcher.exe"
    if (-not (Test-Path -LiteralPath $exeSource)) { throw "找不到 CampusOpportunityWatcher.exe，请从完整发布包内运行。" }
    if (Get-Process -Name "CampusOpportunityWatcher" -ErrorAction SilentlyContinue) { throw "请先关闭校园机会提醒，再运行安装。" }
    $program = Join-Path $dataRoot "CampusOpportunityWatcher.exe"
    Copy-Item -LiteralPath $exeSource -Destination $program -Force
    $prefix = ""
    $workDir = $dataRoot
}

# 先校验学校配置，配置有误就不装计划任务。
$check = Start-Process -FilePath $program -ArgumentList "$prefix--school $School --validate" -WorkingDirectory $workDir -Wait -PassThru -WindowStyle Hidden
if ($check.ExitCode -ne 0) { throw "学校配置「$School」无效或不存在，请先按 AI_SETUP.md 配置并运行 --validate 查看原因。" }

$action = New-ScheduledTaskAction -Execute $program -Argument "$prefix--school $School --scheduled" -WorkingDirectory $workDir
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -MultipleInstances IgnoreNew
$principal = New-ScheduledTaskPrincipal -UserId $env:USERNAME -LogonType Interactive -RunLevel Limited
foreach ($item in @(@("上午", $FirstTime), @("晚上", $SecondTime))) {
    $trigger = New-ScheduledTaskTrigger -Daily -At $item[1]
    Register-ScheduledTask -TaskName "校园机会提醒-$School-$($item[0])" -Action $action -Trigger $trigger `
        -Settings $settings -Principal $principal -Description "检查 $School 的竞赛、实习、出国通知，完成后自动退出" -Force | Out-Null
}

$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut((Join-Path ([Environment]::GetFolderPath("Desktop")) "校园机会提醒-$School.lnk"))
$shortcut.TargetPath = $program
$shortcut.Arguments = "$prefix--school $School"
$shortcut.WorkingDirectory = $workDir
$shortcut.Save()

Start-Process -FilePath $program -ArgumentList "$prefix--school $School" -WorkingDirectory $workDir
Write-Host "安装完成：每天 $FirstTime 和 $SecondTime 自动检查 $School。首次打开后请点“立即检测”建立基线。"
