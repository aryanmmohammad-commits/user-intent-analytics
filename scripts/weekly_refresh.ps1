# Weekly refresh for Revenue Signals, every step in order. Stops at the first failure.
# Run from anywhere:  powershell -ExecutionPolicy Bypass -File scripts\weekly_refresh.ps1
# A scheduler (Windows Task Scheduler, or cron/Airflow in a company) would run this every Monday at 06:00.
Set-Location (Split-Path $PSScriptRoot -Parent)
$py = '.venv-mcp\Scripts\python.exe'

function Step([string]$Name, [scriptblock]$Run) {
  Write-Host "`n--- $Name ---"
  & $Run
  if ($LASTEXITCODE -ne 0) { throw "STOP: '$Name' failed (exit $LASTEXITCODE). Later steps did not run." }
}

Step 'Load call outcomes (ELT load)' { python scripts/load_outcomes.py }
Step 'dbt build (models + tests)'    { dbt build }
Step 'Export marts for Cube'         { python scripts/export_for_cube.py }
Step 'Restart Cube (fresh data)'     {
  docker restart goalearn-cube | Out-Null
  $global:LASTEXITCODE = 1
  for ($i = 0; $i -lt 60; $i++) {
    try { $null = Invoke-WebRequest -Uri 'http://localhost:4000/readyz' -UseBasicParsing -TimeoutSec 3; $global:LASTEXITCODE = 0; break }
    catch { Start-Sleep -Seconds 2 }
  }
}
Step 'Contract check: Cube vs dbt'   { python scripts/check_cube.py }
Step 'Contract check: outcomes'      { python scripts/check_outcomes.py }
Step 'Smoke test: MCP tools'         { & $py scripts/smoke_mcp.py }
Step 'Monday brief (workflow)'       { & $py scripts/monday_brief.py }
Write-Host "`nWeekly refresh done. Brief: briefs\  Scorecards: runs\index.html"
