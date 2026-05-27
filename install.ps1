# TheDawg installer  (Windows / PowerShell)
# -----------------------------------------
# One-line install (paste in PowerShell):
#
#   iwr -useb https://raw.githubusercontent.com/the-priest/theDawg/main/install.ps1 | iex
#
# Or, from a clone:   .\install.ps1
#
# What it does (no admin needed — everything per-user under %LOCALAPPDATA%):
#   - checks for python (>= 3.8)
#   - fetches the repo into %LOCALAPPDATA%\TheDawg
#   - drops a `thedawg.bat` launcher into %LOCALAPPDATA%\Programs\TheDawg
#     and adds that folder to your user PATH
#   - creates a Start Menu shortcut with the Cartman icon
#   - reminds you to set your API key
#
$ErrorActionPreference = "Stop"
$repo    = "the-priest/theDawg"
$branch  = "main"
$srcDir  = Join-Path $env:LOCALAPPDATA "TheDawg"
$binDir  = Join-Path $env:LOCALAPPDATA "Programs\TheDawg"
$launch  = Join-Path $binDir "thedawg.bat"
$smenu   = Join-Path $env:APPDATA "Microsoft\Windows\Start Menu\Programs"

# ---- pretty ----
function say  ($m) { Write-Host ":: $m" -ForegroundColor Yellow }
function ok   ($m) { Write-Host "  v $m" -ForegroundColor Green }
function warn ($m) { Write-Host "  ! $m" -ForegroundColor Red }
function step ($m) { Write-Host "  ... $m" -ForegroundColor DarkGray }

Write-Host ""
Write-Host "  TheDawg installer" -ForegroundColor Yellow -NoNewline
Write-Host "  - $repo" -ForegroundColor DarkGray
Write-Host "  cross-platform Python toolsmith" -ForegroundColor DarkGray
Write-Host ""

# ---- python ----
say "checking python"
$py = $null
foreach ($cmd in @("python","python3","py")) {
  try {
    $v = & $cmd -c "import sys; print('%d.%d' % sys.version_info[:2])" 2>$null
    if ($LASTEXITCODE -eq 0 -and $v) { $py = $cmd; ok "$cmd $v"; break }
  } catch {}
}
if (-not $py) {
  warn "python not found"
  Write-Host "    install it from https://python.org (or the Microsoft Store: 'Python 3.12')"
  Write-Host "    after install, OPEN A NEW POWERSHELL and re-run this command."
  exit 1
}

# ---- fetch source ----
say "fetching source"
New-Item -ItemType Directory -Force -Path $srcDir, $binDir | Out-Null

$selfDir = if ($PSScriptRoot) { $PSScriptRoot } else { "" }
if ($selfDir -and (Test-Path (Join-Path $selfDir "thedawg.py"))) {
  step "using local copy at $selfDir"
  foreach ($item in @("thedawg.py","ui","assets","sounds","README.md","LICENSE")) {
    $src = Join-Path $selfDir $item
    if (Test-Path $src) {
      Copy-Item -Recurse -Force $src $srcDir
    }
  }
  # make sure sounds/ exists even if the source didn't ship it
  $soundsDir = Join-Path $srcDir "sounds"
  if (-not (Test-Path $soundsDir)) { New-Item -ItemType Directory -Force -Path $soundsDir | Out-Null }
} else {
  $tarball = "https://codeload.github.com/$repo/zip/refs/heads/$branch"
  step "downloading $tarball"
  $tmp   = New-TemporaryFile
  $zip   = "$tmp.zip"
  Invoke-WebRequest -UseBasicParsing $tarball -OutFile $zip
  $stage = New-Item -ItemType Directory -Force -Path (Join-Path $env:TEMP "thedawg-stage-$([System.Guid]::NewGuid().ToString('N'))")
  Expand-Archive -Path $zip -DestinationPath $stage -Force
  $inner = Get-ChildItem $stage | Select-Object -First 1
  Copy-Item -Recurse -Force (Join-Path $inner.FullName "*") $srcDir
  Remove-Item -Recurse -Force $stage
  Remove-Item -Force $zip
  Remove-Item -Force $tmp
}
ok "source at $srcDir"

# ---- launcher (.bat) ----
say "writing launcher: $launch"
$pyScript = Join-Path $srcDir "thedawg.py"
@"
@echo off
$py "$pyScript" %*
"@ | Set-Content -Encoding ASCII $launch
ok "launcher: $launch"

# ---- user PATH ----
say "ensuring PATH"
$userPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($userPath -split ";" -contains $binDir)) {
  $newPath = if ([string]::IsNullOrWhiteSpace($userPath)) { $binDir } else { $userPath.TrimEnd(';') + ";" + $binDir }
  [Environment]::SetEnvironmentVariable("Path", $newPath, "User")
  warn "added $binDir to your user PATH"
  Write-Host "    open a NEW PowerShell to pick it up."
} else {
  ok "$binDir already on PATH"
}

# ---- Start Menu shortcut ----
say "creating Start Menu shortcut"
try {
  $ws  = New-Object -ComObject WScript.Shell
  $lnk = $ws.CreateShortcut((Join-Path $smenu "TheDawg.lnk"))
  $lnk.TargetPath       = $launch
  $lnk.WorkingDirectory = $srcDir
  $lnk.Description      = "AI-assisted cross-platform Python toolsmith"
  $iconPng = Join-Path $srcDir "assets\icon-256.png"
  $iconIco = Join-Path $srcDir "assets\icon.ico"
  if (Test-Path $iconIco)       { $lnk.IconLocation = $iconIco }
  elseif (Test-Path $iconPng)   { $lnk.IconLocation = $iconPng }
  $lnk.Save()
  ok "Start Menu: TheDawg"
} catch {
  warn "couldn't create Start Menu shortcut: $_"
}

# ---- key setup hint ----
Write-Host ""
Write-Host "  set your API key" -ForegroundColor Yellow -NoNewline
Write-Host "  (one of these, before launching)"
Write-Host '  $env:GROQ_API_KEY="gsk_..."           ' -NoNewline; Write-Host "# Groq (recommended)" -ForegroundColor DarkGray
Write-Host '  $env:SILICONFLOW_API_KEY="sk-..."     ' -NoNewline; Write-Host "# SiliconFlow"        -ForegroundColor DarkGray
Write-Host '  $env:GOOGLE_API_KEY="AIza..."         ' -NoNewline; Write-Host "# Google AI Studio"   -ForegroundColor DarkGray
Write-Host '  $env:NOVITA_API_KEY="sk_..."          ' -NoNewline; Write-Host "# Novita"             -ForegroundColor DarkGray
Write-Host "  (to persist, set them in Windows Settings -> Edit environment variables, or use [Environment]::SetEnvironmentVariable)" -ForegroundColor DarkGray
Write-Host "  or set them inside TheDawg's Settings panel after first launch." -ForegroundColor DarkGray

# ---- done ----
Write-Host ""
Write-Host "  ready." -ForegroundColor Green -NoNewline
Write-Host "  launch with:"
Write-Host "  thedawg" -ForegroundColor Yellow
Write-Host "  or open it from the Start Menu" -ForegroundColor DarkGray
Write-Host ""
