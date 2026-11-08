function Get-DeskPetPython {
    param([string]$Requested)
    $candidates = @()
    if ($Requested) {
        $candidates += $Requested
    } else {
        $launcher = Get-Command py.exe -CommandType Application -ErrorAction SilentlyContinue
        if ($launcher) {
            $resolved = & $launcher.Source -3 -c 'import sys; print(sys.executable)' 2>$null
            if ($LASTEXITCODE -eq 0) { $candidates += $resolved }
        }
        $candidates += @(Get-Command python.exe -CommandType Application -All -ErrorAction SilentlyContinue |
            Where-Object { $_.Source -notlike '*\Microsoft\WindowsApps\*' } |
            ForEach-Object { $_.Source })
    }
    foreach ($candidate in $candidates) {
        if (!(Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $candidate = (Resolve-Path -LiteralPath $candidate).Path
        & $candidate -c 'import sys; sys.exit(sys.version_info < (3, 10))' 2>$null
        if ($LASTEXITCODE -eq 0) { return $candidate }
    }
    throw 'Python 3.10+ is required. Install Python with Tk, or pass -PythonExe with its full path.'
}

function Invoke-DeskPetManager {
    param([string]$Action, [string]$PythonExe, [string]$DataDir, [string]$CodexHome, [switch]$NoLaunch)
    if ($env:OS -ne 'Windows_NT') { throw 'Codex Desk Pet requires Windows.' }
    if (!$PythonExe -and $Action -ne 'install') {
        $runtime = if ($DataDir) { $DataDir } else { Join-Path $env:LOCALAPPDATA 'CodexDeskPet' }
        $manifest = Join-Path $runtime 'installation.json'
        if (Test-Path -LiteralPath $manifest -PathType Leaf) {
            $installed = Get-Content -LiteralPath $manifest -Raw -Encoding UTF8 | ConvertFrom-Json
            if ($installed.owner -eq 'codex-desk-pet' -and (Test-Path -LiteralPath $installed.python -PathType Leaf)) {
                $PythonExe = $installed.python
            }
        } elseif ($Action -eq 'uninstall') {
            Write-Output 'Codex Desk Pet is not installed.'
            return
        }
    }
    $python = Get-DeskPetPython $PythonExe
    $arguments = @((Join-Path $PSScriptRoot 'manage.py'), $Action)
    if ($DataDir) { $arguments += @('--data-dir', $DataDir) }
    if ($CodexHome) { $arguments += @('--codex-home', $CodexHome) }
    if ($NoLaunch) { $arguments += '--no-launch' }
    & $python @arguments
    if ($LASTEXITCODE -ne 0) { throw "Codex Desk Pet $Action failed (exit $LASTEXITCODE)." }
}
