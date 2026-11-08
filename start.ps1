[CmdletBinding()]
param([string]$PythonExe, [string]$DataDir)
$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'desk-pet.ps1')
Invoke-DeskPetManager -Action start -PythonExe $PythonExe -DataDir $DataDir
