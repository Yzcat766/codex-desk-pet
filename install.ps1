[CmdletBinding()]
param([string]$PythonExe, [string]$DataDir, [string]$CodexHome, [switch]$NoLaunch)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'desk-pet.ps1')
$codex = Get-Command codex -ErrorAction SilentlyContinue
if ($codex) {
    try {
        & $codex --version
        if ($LASTEXITCODE -ne 0) { Write-Warning 'Could not determine Codex CLI version.' }
    } catch { Write-Warning 'Could not run Codex CLI to check its version.' }
} else {
    $desktop = Get-AppxPackage -Name 'OpenAI.Codex' -ErrorAction SilentlyContinue
    if ($desktop) {
        Write-Output "Desktop package: $($desktop.Name) $($desktop.Version)"
    } else {
        Write-Warning 'Codex was not detected. Install a Codex client with lifecycle hooks to receive events.'
    }
}
Invoke-DeskPetManager -Action install -PythonExe $PythonExe -DataDir $DataDir -CodexHome $CodexHome -NoLaunch:$NoLaunch
