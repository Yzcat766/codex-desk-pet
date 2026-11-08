[CmdletBinding()]
param([string]$PythonExe, [string]$DataDir)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'desk-pet.ps1')
Invoke-DeskPetManager -Action uninstall -PythonExe $PythonExe -DataDir $DataDir
