# Sync sdk/src/pam -> plugin/vendor/pam
$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$src = Join-Path $root "sdk\src\pam"
$dest = Join-Path $root "plugin\vendor\pam"

if (-not (Test-Path $src)) {
    throw "Missing SDK source: $src"
}

New-Item -ItemType Directory -Force -Path $dest | Out-Null
robocopy $src $dest /MIR /XD __pycache__ /XF *.pyc /NFL /NDL /NJH /NJS /nc /ns /np | Out-Null
if ($LASTEXITCODE -gt 7) { exit $LASTEXITCODE }

Write-Host "Synced $src -> $dest"
