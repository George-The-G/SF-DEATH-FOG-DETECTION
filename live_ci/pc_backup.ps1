# PC backup runner (multi-scale guide 10, part C). Run by Windows Task Scheduler every 10 minutes; exits at once when the published data is fresh.
# It never pushes to git and never deploys Pages. NOT installed automatically: George approves first.
#
# Install (run once, in an elevated or normal PowerShell; "run whether user is logged on or not" needs the password prompt of -User):
#   $a = New-ScheduledTaskAction -Execute "powershell.exe" -Argument "-NoProfile -ExecutionPolicy Bypass -File C:\Users\George\deathfog-data\backup_worktree\live_ci\pc_backup.ps1"
#   $t = New-ScheduledTaskTrigger -Once -At (Get-Date) -RepetitionInterval (New-TimeSpan -Minutes 10)
#   $s = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 8) -MultipleInstances IgnoreNew -StartWhenAvailable
#   Register-ScheduledTask -TaskName "deathfog-backup" -Action $a -Trigger $t -Settings $s
# Remove:
#   Unregister-ScheduledTask -TaskName "deathfog-backup" -Confirm:$false
#
# R2 secrets (R2_ACCOUNT_ID, R2_ACCESS_KEY_ID, R2_SECRET_ACCESS_KEY, R2_BUCKET) are read from user environment variables; without them the build is kept locally and not published.
$ErrorActionPreference = "Continue"
$repo   = "C:\Users\George\source\repos\SF-DEATH-FOG-DETECTION"
$wt     = "C:\Users\George\deathfog-data\backup_worktree"      # own worktree, detached at origin/v3-dev; never the main checkout
$py     = Join-Path $repo ".venv\Scripts\python.exe"
$logdir = "C:\Users\George\deathfog-data\backup"
New-Item -ItemType Directory -Force $logdir | Out-Null
$log = Join-Path $logdir "backup.log"
function Log($m) { "$(Get-Date -Format s) $m" | Add-Content -Path $log -Encoding utf8 }

# keep the log small
if ((Test-Path $log) -and (Get-Item $log).Length -gt 1MB) { Move-Item -Force $log "$log.old" }

# below normal priority: the build is CPU only and must not disturb GPU work
(Get-Process -Id $PID).PriorityClass = "BelowNormal"

# update the worktree only when a build may be needed (cheap check first: the python entry decides freshness itself with --dry-run)
if (Test-Path $wt) { } else { git -C $repo worktree add --detach $wt origin/v3-dev 2>&1 | Out-Null }
Push-Location $wt
try {
  $env:PYTHONPATH = $wt
  $dry = & $py -m microclimate.live backup --dry-run 2>&1
  Log ($dry -join " | ")
  if ($dry -match ": fresh") { exit 0 }
  git -C $repo fetch -q origin v3-dev 2>&1 | Out-Null
  git -C $wt checkout -q --detach origin/v3-dev 2>&1 | Out-Null
  $res = & $py -m microclimate.live backup 2>&1
  $code = $LASTEXITCODE
  Log ("exit $code : " + ($res -join " | "))
  exit $code
} finally { Pop-Location }
