<#
.SYNOPSIS
    Signs tvdinner's Windows executable and installer, and publishes the
    draft release.

.DESCRIPTION
    Certum's SimplySign opens a signing session only after a one-time code
    from its mobile app, and that session belongs to the machine it was
    opened on — so a GitHub-hosted runner can never sign. CI therefore
    builds the PyInstaller bundle and attaches it to a *draft* release; this
    script does the rest here, where the certificate is:

        download the bundle  →  sign tvdinner.exe  →  build the installer
        →  sign the installer  →  upload  →  publish

    tvdinner.exe is signed before Inno packages it, which is the whole reason
    the installer can't be built in CI: the executable people actually run
    every day is the one SmartScreen and Defender judge, and signing only the
    installer leaves it unsigned.

    Requires: SimplySign Desktop running and logged in, signtool.exe from the
    Windows SDK, Inno Setup 6, and the GitHub CLI (`gh`) authenticated. Run it
    from the root of a checkout at the tag being released.

.PARAMETER Tag
    The release tag, e.g. v1.43.0.

.PARAMETER Thumbprint
    SHA-1 thumbprint of the signing certificate, passed to signtool as
    /sha1. The unambiguous way to choose, and worth preferring: signtool
    searches your user certificate store, and if anything else there can
    sign code, /a picks between them on a heuristic Microsoft doesn't
    document. Matches how scripts/sign-windows-release.ps1 does it in
    loadbearer.

.PARAMETER Subject
    Certificate subject substring, passed to signtool as /n. A looser
    alternative to -Thumbprint.

    With neither, signtool is left to pick with /a -- fine when SimplySign's
    is the only certificate loaded. Whichever is used, /v makes signtool
    print the certificate it actually chose.

.PARAMETER Publish
    Publish the draft release once the signed installer is uploaded.

.EXAMPLE
    .\windows\sign-release.ps1 -Tag v1.43.0 -Publish

.EXAMPLE
    .\windows\sign-release.ps1 -Tag v1.43.0 -Thumbprint AB12CD34... -Publish
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$Tag,
    [string]$Thumbprint,
    [string]$Subject,
    [switch]$Publish
)

$ErrorActionPreference = 'Stop'

# Every failure check below reads $LASTEXITCODE. If this preference is on,
# a native command exiting nonzero throws before the check is reached --
# which would break the double-sign guard, where a nonzero `signtool verify`
# is the *expected* result. Off by default in both Windows PowerShell 5.1
# (where the variable doesn't exist at all) and PowerShell 7, but pin it:
# failing halfway costs a SimplySign session, not just a rerun.
$PSNativeCommandUseErrorActionPreference = $false

# Certum's own timestamp authority. Timestamping is what keeps a signature
# valid after the certificate expires, so it is not optional.
$TimestampUrl = 'http://time.certum.pl'

function Find-Tool {
    param([string]$Name, [string[]]$SearchRoots, [string]$Hint)

    $onPath = Get-Command $Name -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    foreach ($root in $SearchRoots) {
        if (-not $root -or -not (Test-Path $root)) { continue }
        $found = Get-ChildItem -Path $root -Filter $Name -Recurse -ErrorAction SilentlyContinue |
            Where-Object { $_.FullName -notmatch '\\arm' } |
            Sort-Object @{ Expression = { $_.FullName -match '\\x64\\' }; Descending = $true },
                        @{ Expression = { $_.LastWriteTime }; Descending = $true }
        if ($found) { return $found[0].FullName }
    }
    throw "$Name not found. $Hint"
}

function Get-InstallLocation {
    <# Ask the installer where it put things, rather than guessing at a
       default that a per-user install or a 64-bit build won't match. Inno
       Setup writes InstallLocation to its own uninstall key; check the
       32-bit registry view, the 64-bit one, and a per-user install. #>
    param([string]$KeyName)

    $roots = @(
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall'
    )
    foreach ($root in $roots) {
        # Plain concatenation, not Join-Path: that validates the drive and
        # errors outright where a registry drive isn't mounted.
        $location = (Get-ItemProperty -Path "$root\$KeyName" `
            -Name InstallLocation -ErrorAction SilentlyContinue).InstallLocation
        if ($location -and (Test-Path $location)) { return $location }
    }
    return $null
}

function Join-IfSet {
    param([string]$Base, [string]$Child)
    if ($Base) { Join-Path $Base $Child } else { $null }
}

$signtool = Find-Tool -Name 'signtool.exe' -SearchRoots @(
    'C:\Program Files (x86)\Windows Kits\10\bin',
    'C:\Program Files\Windows Kits\10\bin'
) -Hint 'Install the Windows SDK, or put signtool.exe on PATH.'

$iscc = Find-Tool -Name 'ISCC.exe' -SearchRoots @(
    (Get-InstallLocation -KeyName 'Inno Setup 6_is1'),
    'C:\Program Files (x86)\Inno Setup 6',
    'C:\Program Files\Inno Setup 6',
    (Join-IfSet $env:LOCALAPPDATA 'Programs\Inno Setup 6'),
    (Join-IfSet $env:ProgramData 'chocolatey\lib\innosetup\tools')
) -Hint 'Install Inno Setup 6 (choco install innosetup), or put ISCC.exe on PATH.'

Write-Host "signtool:  $signtool"
Write-Host "ISCC:      $iscc"

if (-not (Test-Path 'windows\tvdinner.iss')) {
    throw 'Run this from the root of the repository, checked out at the tag being released.'
}

function Invoke-Sign {
    param([string]$Path, [string]$What)

    # Refuse to double-sign: stacking a second signature is a quiet way to
    # ship something nobody meant to.
    & $signtool verify /pa /q $Path 2>$null
    if ($LASTEXITCODE -eq 0) { throw "$What is already signed." }

    $signArgs = @('sign')
    if ($Thumbprint)  { $signArgs += @('/sha1', $Thumbprint) }
    elseif ($Subject) { $signArgs += @('/n', $Subject) }
    else              { $signArgs += '/a' }
    $signArgs += @('/fd', 'sha256', '/tr', $TimestampUrl, '/td', 'sha256', '/v', $Path)

    Write-Host "Signing $What..."
    & $signtool @signArgs
    if ($LASTEXITCODE -ne 0) { throw "Signing $What failed. Is SimplySign Desktop logged in?" }

    & $signtool verify /pa /v $Path
    if ($LASTEXITCODE -ne 0) { throw "$What did not verify after signing." }
}

# --- the bundle CI built ----------------------------------------------------

$work = Join-Path ([System.IO.Path]::GetTempPath()) "tvdinner-sign-$Tag"
if (Test-Path $work) { Remove-Item $work -Recurse -Force }
New-Item -ItemType Directory -Path $work | Out-Null

Write-Host "Downloading the bundle from $Tag..."
& gh release download $Tag --pattern '*-windows-bundle.zip' --dir $work
if ($LASTEXITCODE -ne 0) { throw "No bundle on $Tag. Is SIGN_WINDOWS_LOCALLY set on the repo?" }

$bundle = Get-ChildItem -Path $work -Filter '*-windows-bundle.zip' | Select-Object -First 1
$version = [regex]::Match($bundle.Name, '^tvdinner-(.+)-windows-bundle\.zip$').Groups[1].Value
if (-not $version) { throw "Couldn't read a version out of $($bundle.Name)." }
Write-Host "Version:   $version"

# The .iss packages ..\dist\tvdinner, so put it exactly there.
if (Test-Path 'dist\tvdinner') { Remove-Item 'dist\tvdinner' -Recurse -Force }
New-Item -ItemType Directory -Path 'dist\tvdinner' -Force | Out-Null
Expand-Archive -Path $bundle.FullName -DestinationPath 'dist\tvdinner' -Force

# --- sign the app, then package it, then sign the package -------------------

Invoke-Sign -Path (Resolve-Path 'dist\tvdinner\tvdinner.exe') -What 'tvdinner.exe'

# Only our own executable. libmpv-2.dll and the Python runtime DLLs are third
# party; re-signing someone else's binary asserts a provenance we don't have.

Write-Host "Building the installer..."
New-Item -ItemType Directory -Force -Path dist_installer | Out-Null
& $iscc 'windows\tvdinner.iss' "/DMyAppVersion=$version"
if ($LASTEXITCODE -ne 0) { throw 'Inno Setup failed.' }

$installer = Get-ChildItem -Path dist_installer -Filter '*.exe' | Select-Object -First 1
if (-not $installer) { throw 'Inno Setup produced no installer.' }

Invoke-Sign -Path $installer.FullName -What $installer.Name

$hash = (Get-FileHash -Algorithm SHA256 $installer.FullName).Hash
Write-Host ''
Write-Host "Signed SHA-256: $hash"
Write-Host '(winget manifests pin this; the auto-submit workflow reads it from'
Write-Host ' the published release, so publish before it runs.)'
Write-Host ''

# --- put it on the release --------------------------------------------------

Write-Host "Uploading the signed installer to $Tag..."
& gh release upload $Tag $installer.FullName --clobber
if ($LASTEXITCODE -ne 0) { throw 'Upload failed.' }

# The bundle was only ever a hand-off to this script.
Write-Host 'Removing the bundle from the release...'
& gh release delete-asset $Tag $bundle.Name --yes
if ($LASTEXITCODE -ne 0) { Write-Warning "Couldn't remove $($bundle.Name); delete it by hand." }

if ($Publish) {
    Write-Host "Publishing $Tag..."
    & gh release edit $Tag --draft=false
    if ($LASTEXITCODE -ne 0) { throw "Could not publish $Tag." }
    Write-Host 'Published.'
} else {
    Write-Host "Draft left unpublished. Publish with: gh release edit $Tag --draft=false"
}
