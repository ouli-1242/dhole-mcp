# P0.4 服务预检：9217(dhole) / 8399(靶站) 缺谁起谁，env 与宪章一致；重启事件由调用方记 env-log.md
#
# env 修复沿用 R01-005（见 review/round-01/ensure_up.ps1 顶注）：env 在本进程顶层
# 真赋值，Start-Process 子进程继承；不写进可展开字符串。
$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent (Split-Path -Parent $PSCommandPath)  # review/round-17 -> review
$repo = Split-Path -Parent $root
Set-Location $repo
$env:DHOLE_IGNORE_ROBOTS = '1'
$env:DHOLE_USAGE_LOG = '1'
if (-not (Get-NetTCPConnection -LocalPort 8399 -State Listen)) {
  Start-Process -WindowStyle Hidden pwsh -ArgumentList '-NoProfile','-Command',"python evals/fixture_site.py *> review/.runtime/fixture-8399.log"
  "fixture-8399 STARTED $(Get-Date -Format o)" | Add-Content review/.runtime/server-restarts.log
  Start-Sleep 3
}
if (-not (Get-NetTCPConnection -LocalPort 9217 -State Listen)) {
  Start-Process -WindowStyle Hidden pwsh -ArgumentList '-NoProfile','-Command',"dhole --http --host 127.0.0.1 --port 9217 *> review/.runtime/dhole-9217.log"
  "dhole-9217 STARTED $(Get-Date -Format o)" | Add-Content review/.runtime/server-restarts.log
  Start-Sleep 6
}
if (-not (Get-NetTCPConnection -LocalPort 8398 -State Listen)) {
  Start-Process -WindowStyle Hidden pwsh -ArgumentList '-NoProfile','-Command',"python -m http.server 8398 --bind 127.0.0.1 --directory review/round-17/site *> review/.runtime/site-8398.log"
  "site-8398 STARTED $(Get-Date -Format o)" | Add-Content review/.runtime/server-restarts.log
  Start-Sleep 2
}
"9217=" + (Get-NetTCPConnection -LocalPort 9217 -State Listen).Count + " 8399=" + (Get-NetTCPConnection -LocalPort 8399 -State Listen).Count + " 8398=" + (Get-NetTCPConnection -LocalPort 8398 -State Listen).Count
