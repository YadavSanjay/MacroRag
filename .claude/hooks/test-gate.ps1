# Stop hook (Production Hardening §L dev-harness + coding standards). Runs only when needed:
# 1. Coding standards (advisory): tools/standards/check_standards.py stop --hook -- ruff on
#    changed files, Google docstrings on changed lines, the ratchet check when lint config
#    changed. It skips itself when nothing relevant changed and only reports while advisory.
# 2. Fast pytest subset (blocking): the same three-file subset as before, run only when .py
#    files under src/ or test/ changed vs HEAD AND differ from the last green run (fingerprint in
#    the gitignored .claude/state/test-gate.json). Exits 2 (reason -> stderr) to block; 0 to
#    allow. Guards against a loop via stop_hook_active. Fails open if the env is missing.
$ErrorActionPreference = "SilentlyContinue"
[Console]::OutputEncoding = [System.Text.Encoding]::UTF8
$raw = [Console]::In.ReadToEnd()
try { $j = $raw | ConvertFrom-Json } catch { $j = $null }
if ($j -and $j.stop_hook_active) { exit 0 }   # already re-invoked after a block -> don't loop

# MACRORAG_HOOK_PYTHON, else the py312 conda env (never the bare python on PATH: it lacks the deps).
$py = $env:MACRORAG_HOOK_PYTHON
if (-not $py) {
    $conda = (Get-Command conda -ErrorAction SilentlyContinue).Source
    if ($conda) { $py = Join-Path (Split-Path (Split-Path $conda)) "envs\py312\python.exe" }
}
if (-not $py -or -not (Test-Path $py)) { exit 0 }   # env missing -> fail open, don't wedge the session
$env:PYTHONIOENCODING = "utf-8"

# 1. Coding standards (the checker decides advisory vs block; it reports via its hook JSON).
$messages = @()
$block = $null
$std = ($raw | & $py tools/standards/check_standards.py stop --hook 2>$null) -join "`n"
if ($std) {
    try {
        $resp = $std | ConvertFrom-Json
        if ($resp.decision -eq "block") { $block = $resp.reason }
        elseif ($resp.systemMessage) { $messages += $resp.systemMessage }
    } catch {}
}

# 2. Fast pytest subset, only for changed .py files not already seen green.
$changed = @(
    (git diff --name-only HEAD 2>$null) + (git ls-files --others --exclude-standard 2>$null) |
        ForEach-Object { $_.Trim().Replace("\", "/") } |
        Where-Object { $_ -match '^(src|test)/.*\.py$' } |
        Sort-Object -Unique
)
$state = ".claude/state/test-gate.json"
$fingerprint = ""
if ($changed.Count -gt 0) {
    $parts = foreach ($f in $changed) {
        $h = if (Test-Path $f) { (Get-FileHash -Algorithm SHA256 $f).Hash } else { "missing" }
        "$f=$h"
    }
    $bytes = [System.Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))
    $sha = [System.Security.Cryptography.SHA256]::Create()
    $fingerprint = [System.BitConverter]::ToString($sha.ComputeHash($bytes)).Replace("-", "")
}
$lastGreen = $null
try { $lastGreen = (Get-Content $state -Raw | ConvertFrom-Json).green } catch {}

if ($fingerprint -and $fingerprint -ne $lastGreen) {
    $out = & $py -m pytest test/test_retriever_pipeline.py test/test_synthesis.py test/test_api.py -q 2>&1
    if ($LASTEXITCODE -ne 0) {
        [Console]::Error.WriteLine("Stop blocked by dev-harness (SS-L): fast test subset is RED -- fix before ending.")
        [Console]::Error.WriteLine(($out | Select-Object -Last 15) -join "`n")
        foreach ($m in $messages) { [Console]::Error.WriteLine("`n$m") }
        exit 2
    }
    New-Item -ItemType Directory -Force (Split-Path $state) | Out-Null
    Set-Content -Path $state -Value (@{ green = $fingerprint } | ConvertTo-Json -Compress) -Encoding ASCII
}

if ($block) {
    [Console]::Error.WriteLine($block)
    exit 2
}
if ($messages.Count -gt 0) {
    [Console]::Out.Write((@{ systemMessage = ($messages -join "`n`n") } | ConvertTo-Json -Compress))
}
exit 0
