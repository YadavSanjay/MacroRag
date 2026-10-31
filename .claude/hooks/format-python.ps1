# PostToolUse hook (Production Hardening §L dev-harness): after an Edit/Write to a .py file,
# run ruff's critical-subset auto-fix (the same rules CI enforces via ruff.toml) so
# lint issues like unused imports are cleaned up immediately, keeping the CI gate
# green. Best-effort and non-blocking (always exits 0); never touches non-Python.
$ErrorActionPreference = "SilentlyContinue"
$raw = [Console]::In.ReadToEnd()
try { $j = $raw | ConvertFrom-Json } catch { exit 0 }
$fp = [string]$j.tool_input.file_path
if ($fp -and $fp.ToLower().EndsWith(".py") -and (Test-Path $fp)) {
    # MACRORAG_HOOK_RUFF, else ruff on PATH, else the py312 conda env's ruff.
    $ruff = $env:MACRORAG_HOOK_RUFF
    if (-not $ruff) { $ruff = (Get-Command ruff -ErrorAction SilentlyContinue).Source }
    if (-not $ruff) {
        $conda = (Get-Command conda -ErrorAction SilentlyContinue).Source
        if ($conda) { $ruff = Join-Path (Split-Path (Split-Path $conda)) "envs\py312\Scripts\ruff.exe" }
    }
    if ($ruff -and (Test-Path $ruff)) {
        $env:PYTHONIOENCODING = "utf-8"
        & $ruff check --fix --quiet $fp 2>$null | Out-Null
    }
}
exit 0
