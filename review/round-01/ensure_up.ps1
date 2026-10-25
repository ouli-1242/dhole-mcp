# P0.4 服务预检：9201(dhole) / 8399(靶站) 缺谁起谁，env 与宪章一致；重启事件由调用方记 env-log.md
#
# R01-005（2026-10-25）：env 赋值曾写在本文件第 12 行那串双引号里 —— PowerShell 的
# 可展开字符串在**外层**求值，`$env:DHOLE_IGNORE_ROBOTS` 未设时展开成空串，子进程
# 收到的是 `='1'; dhole …`。机器人门照常工作（env 根本没设），P03/P07 的
# robots_disallowed 与缓存回执的「bypassed (process-wide)」自相矛盾——两个进程、
# 两份 env，各自诚实。修法：env 在本进程顶层真赋值，Start-Process 的子进程继承。
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)  # review/round-01 -> review
$repo = Split-Path -Parent $root
Set-Location $repo
$env:DHOLE_IGNORE_ROBOTS = '1'
$env:DHOLE_USAGE_LOG = '1'
if (-not (Get-NetTCPConnection -LocalPort 8399 -State Listen)) {
  Start-Process -WindowStyle Hidden pwsh -ArgumentList '-NoProfile','-Command',"python evals/fixture_site.py *> review/.runtime/fixture-8399.log"
  "fixture-8399 STARTED $(Get-Date -Format o)" | Add-Content review/.runtime/server-restarts.log
  Start-Sleep 3
}
if (-not (Get-NetTCPConnection -LocalPort 9201 -State Listen)) {
  Start-Process -WindowStyle Hidden pwsh -ArgumentList '-NoProfile','-Command',"dhole --http --host 127.0.0.1 --port 9201 *> review/.runtime/dhole-9201.log"
  "dhole-9201 STARTED $(Get-Date -Format o)" | Add-Content review/.runtime/server-restarts.log
  Start-Sleep 6
}
"9201=" + (Get-NetTCPConnection -LocalPort 9201 -State Listen).Count + " 8399=" + (Get-NetTCPConnection -LocalPort 8399 -State Listen).Count
