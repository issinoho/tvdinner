<#
.SYNOPSIS
    Signs a release's Windows installer with the Certum certificate and puts
    the signed copy back on the GitHub Release.

.DESCRIPTION
    The release workflow can't sign: Certum's SimplySign opens a signing
    session only after a one-time code from the mobile app, and that session
    lives on the machine it was opened on — so an ephemeral GitHub-hosted
    runner can never hold one. Signing happens here instead, on the machine
    where SimplySign Desktop is running.

    Because of that, a release is created as a *draft* when
    SIGN_WINDOWS_LOCALLY is set on the repository. Run this against the draft,
    then publish it — so a public release never carries an unsigned installer.

    Requires: SimplySign Desktop running and logged in, signtool.exe from the
    Windows SDK, and the GitHub CLI (`gh`) authenticated.

.PARAMETER Tag
    The release tag, e.g. v1.43.0.

.PARAMETER Subject
    Optional certificate subject substring, passed to signtool as /n. Only
    needed when more than one signing certificate is visible; by default
    signtool picks the best candidate itself with /a.

.PARAMETER Publish
    Publish the draft release once the signed installer is uploaded.

.EXAMPLE
    .\windows\sign-release.ps1 -Tag v1.43.0 -Publish
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Subject,
    [switch]$Publish
)

$ErrorActionPreference = 'Stop'

# Certum's own timestamp authority. Timestamping is what keeps the signature
# valid after the certificate expires, so it is not optional.
$TimestampUrl = 'http://time.certum.pl'

function Find-SignTool {
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    # Newest SDK build first; x64 in preference to x86.
    $candidates = Get-ChildItem -Path 'C:\Program Files (x86)\Windows Kits\10\bin' `
        -Filter signtool.exe -Recurse -ErrorAction SilentlyContinue |
        Where-Object { $_.FullName -match '\\(x64|x86)\\' } |
        Sort-Object @{ Expression = { $_.FullName -match '\\x64\\' } ; Descending = $true },
                    @{ Expression = { $_.Directory.Parent.Name } ; Descending = $true }

    if (-not $candidates) {
        throw "signtool.exe not found. Install the Windows SDK, or put it on PATH."
    }
    return $candidates[0].FullName
}

$signtool = Find-SignTool
Write-Host "signtool:  $signtool"

$work = Join-Path ([System.IO.Path]::GetTempPath()) "tvdinner-sign-$Tag"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null

Write-Host "Downloading the installer from $Tag..."
& gh release download $Tag --pattern '*.exe' --dir $work
if ($LASTEXITCODE -ne 0) { throw "Could not download the installer for $Tag." }

$installer = Get-ChildItem -Path $work -Filter '*.exe' | Select-Object -First 1
if (-not $installer) { throw "No .exe in the $Tag release." }
Write-Host "Installer: $($installer.Name)"

# Refuse to double-sign: a second signature on an already-signed file is a
# silent way to end up with something nobody meant to ship.
& $signtool verify /pa /q $installer.FullName 2>$null
if ($LASTEXITCODE -eq 0) {
    throw "$($installer.Name) is already signed. Nothing to do."
}

$signArgs = @('sign')
if ($Subject) { $signArgs += @('/n', $Subject) } else { $signArgs += '/a' }
$signArgs += @('/fd', 'sha256', '/tr', $TimestampUrl, '/td', 'sha256', '/v', $installer.FullName)

Write-Host "Signing (SimplySign Desktop must be running and logged in)..."
& $signtool @signArgs
if ($LASTEXITCODE -ne 0) { throw "Signing failed. Is SimplySign Desktop logged in?" }

Write-Host "Verifying..."
& $signtool verify /pa /v $installer.FullName
if ($LASTEXITCODE -ne 0) { throw "The signature did not verify." }

$hash = (Get-FileHash -Algorithm SHA256 $installer.FullName).Hash
Write-Host ""
Write-Host "Signed SHA-256: $hash"
Write-Host "(winget manifests pin this; the auto-submit workflow reads it from"
Write-Host " the published release, so publish before it runs.)"
Write-Host ""

Write-Host "Uploading the signed installer back to $Tag..."
& gh release upload $Tag $installer.FullName --clobber
if ($LASTEXITCODE -ne 0) { throw "Upload failed." }

if ($Publish) {
    Write-Host "Publishing $Tag..."
    & gh release edit $Tag --draft=false
    if ($LASTEXITCODE -ne 0) { throw "Could not publish $Tag." }
    Write-Host "Published."
} else {
    Write-Host "Draft left unpublished. Publish with: gh release edit $Tag --draft=false"
}
