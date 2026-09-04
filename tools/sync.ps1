<#
.SYNOPSIS
    Syncs C:\Dev\videoredactor from the newest videoredactor*.zip drop in
    C:\Temp\_AI Coding\, commits, and pushes to origin/main.
    See C:\Dev\_shared-tools\sync-from-zip.ps1 for how this actually works.

.EXAMPLE
    powershell -File tools\sync.ps1
.EXAMPLE
    powershell -File tools\sync.ps1 -ZipPath "C:\Temp\_AI Coding\videoredactor_with_redactor_common.zip"
.EXAMPLE
    powershell -File tools\sync.ps1 -NoPush
#>
[CmdletBinding()]
param(
    [string]$ZipPath,
    [switch]$NoPush
)

& "C:\Dev\_shared-tools\sync-from-zip.ps1" `
    -ZipPath $ZipPath `
    -SourceDir "C:\Temp\_AI Coding" `
    -ZipNamePattern "videoredactor" `
    -RepoPath "C:\Dev\videoredactor" `
    -ProjectDirName "videoredactor" `
    -Marker "main.py" `
    -NoPush:$NoPush
