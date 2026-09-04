<#
.SYNOPSIS
    Signs and publishes a tvdinner release. The one command to run after
    pushing a version tag.

.DESCRIPTION
    A front door to windows/sign-release.ps1, which does the real work.
    This script turns a version number into a tag, supplies the
    certificate thumbprint, and -- the actual point of it -- checks
    everything checkable *before* a signing session is spent.

    That preflight matters because signing happens in the middle of the
    run: tvdinner.exe is signed, then Inno builds the installer around
    it, then the installer is signed too. Anything that fails after the
    first signature means opening a fresh SimplySign session (another
    one-time code from the phone) and starting again. So the checks
    below cover what actually goes wrong -- not logged in, wrong
    checkout, no draft to sign, gh not authenticated -- rather than
    letting signtool discover them halfway.

    Run it from anywhere; it locates the repository from its own path.

.PARAMETER Version
    The version to publish, e.g. 1.43.0. A leading "v" is accepted, so
    either the version or the tag name works.

.PARAMETER Thumbprint
    Signing certificate, defaulting to the one recorded in
    CODE_SIGNING_POLICY.md. Update both after a renewal.

.PARAMETER NoPublish
    Sign and upload, but leave the release as a draft to inspect. Publish
    it afterwards with: gh release edit v<version> --draft=false

.PARAMETER SkipVersionCheck
    Publish even though the working tree isn't at the version being
    released. See the check itself for why that is normally refused.

.EXAMPLE
    .\windows\publish-tvdinner.ps1 1.43.0

.EXAMPLE
    .\windows\publish-tvdinner.ps1 1.43.0 -NoPublish
#>
[CmdletBinding()]
param(
    [Parameter(Mandatory = $true, Position = 0)][string]$Version,
    [string]$Thumbprint = '6B58FE5ED40A67A23A27BEB25C4337ADEA26B9F9',
    [switch]$NoPublish,
    [switch]$SkipVersionCheck
)

$ErrorActionPreference = 'Stop'

# This script exists to tell you what's wrong, so say it plainly rather
# than wrapped in the stack frame PowerShell prints by default. Every
# throw below is written to be read on its own, and the exit code still
# distinguishes a refusal from a success.
trap {
    Write-Host ""
    Write-Host "  $($_.Exception.Message)" -ForegroundColor Red
    Write-Host ""
    exit 1
}

# The checks below read $LASTEXITCODE; see the same note in
# sign-release.ps1 for why this is pinned rather than assumed.
$PSNativeCommandUseErrorActionPreference = $false

# "1.43.0", "v1.43.0" and a pasted tag all mean the same release.
$Version = $Version.Trim().TrimStart('v', 'V')
if ($Version -notmatch '^\d+\.\d+\.\d+') {
    throw "'$Version' doesn't look like a version number -- expected something like 1.43.0."
}
$tag = "v$Version"

$repo = Split-Path -Parent $PSScriptRoot
$signScript = Join-Path $PSScriptRoot 'sign-release.ps1'
if (-not (Test-Path $signScript)) {
    throw "sign-release.ps1 isn't next to this script ($PSScriptRoot). Is the checkout complete?"
}

function Get-TreeVersion {
    <# The version the working tree would build, which is not necessarily
       the one being published. #>
    param([string]$RepoRoot)

    $initPath = Join-Path $RepoRoot 'src/tvdinner/__init__.py'
    if (-not (Test-Path $initPath)) { return $null }
    $match = Select-String -Path $initPath -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
    if (-not $match) { return $null }
    return $match.Matches[0].Groups[1].Value
}

Write-Host ""
Write-Host "Publishing tvdinner $tag" -ForegroundColor Cyan
Write-Host "Repository: $repo"
Write-Host ""
Write-Host "Preflight" -ForegroundColor Cyan

Push-Location $repo
try {
    # 1. gh, since every release operation goes through it.
    if (-not (Get-Command gh -ErrorAction SilentlyContinue)) {
        throw "GitHub CLI (gh) isn't on PATH. Install it from https://cli.github.com/."
    }
    & gh auth status 2>&1 | Out-Null
    if ($LASTEXITCODE -ne 0) { throw "gh isn't authenticated. Run: gh auth login" }
    Write-Host "  gh authenticated"

    # 2. The installer is compiled from this working tree -- the .iss, the
    #    licence, THIRD_PARTY_NOTICES -- while only the *executable* comes
    #    from the release bundle. A checkout on the wrong commit therefore
    #    packages the right binary with the wrong everything else, silently.
    $treeVersion = Get-TreeVersion -RepoRoot $repo
    if (-not $treeVersion) {
        Write-Warning "Couldn't read __version__ from the working tree; skipping the checkout check."
    }
    elseif ($treeVersion -ne $Version) {
        $message = "This checkout is $treeVersion, but you're publishing $Version. " +
                   "The installer is built from the working tree, so it would package $treeVersion's " +
                   "installer script around $Version's executable. Check out $tag first, " +
                   "or pass -SkipVersionCheck if you know the difference is harmless."
        if ($SkipVersionCheck) { Write-Warning $message } else { throw $message }
    }
    else {
        Write-Host "  working tree is at $Version"
    }

    # 3. A draft carrying the bundle is exactly what CI leaves behind, so
    #    its absence says which step hasn't happened yet.
    $json = & gh release view $tag --json isDraft,assets 2>$null
    if ($LASTEXITCODE -ne 0) {
        throw "There's no release $tag. Push the tag and let the release workflow finish first."
    }
    $release = $json | ConvertFrom-Json

    $installer = $release.assets | Where-Object { $_.name -like 'tvdinner-setup-*.exe' }
    $bundle = $release.assets | Where-Object { $_.name -like '*-windows-bundle.zip' }

    if (-not $bundle) {
        if ($installer) {
            throw "$tag already carries $($installer.name) -- it looks like this release has been signed already. Signing it again would need a fresh build."
        }
        throw "$tag has no Windows bundle attached. Either the build hasn't finished, or the repository variable SIGN_WINDOWS_LOCALLY isn't set to true (without it CI publishes an unsigned installer directly)."
    }
    if (-not $release.isDraft) {
        Write-Warning "$tag is already published, but still carries the bundle. Continuing: the signed installer will be added to the public release."
    }
    Write-Host "  $tag is a draft carrying $($bundle.name)"

    # 4. The certificate is only in the store while a SimplySign session is
    #    open, so this doubles as the "are you logged in?" check -- and it
    #    is the one worth failing on early, before anything is built.
    $cert = Get-ChildItem Cert:\CurrentUser\My -ErrorAction SilentlyContinue |
        Where-Object { $_.Thumbprint -eq $Thumbprint }
    if (-not $cert) {
        throw "Certificate $Thumbprint isn't in your certificate store. Open SimplySign Desktop and log in, then try again. (List what is there with: Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert)"
    }
    $daysLeft = [int]($cert.NotAfter - (Get-Date)).TotalDays
    if ($daysLeft -lt 0) {
        throw "Certificate $Thumbprint expired on $($cert.NotAfter.ToString('yyyy-MM-dd'))."
    }
    if ($daysLeft -lt 30) {
        Write-Warning "Certificate expires in $daysLeft days, on $($cert.NotAfter.ToString('yyyy-MM-dd')). Renew it, then update the default in this script and in CODE_SIGNING_POLICY.md."
    }
    Write-Host "  certificate present: $($cert.Subject.Split(',')[0]) (expires $($cert.NotAfter.ToString('yyyy-MM-dd')))"

    Write-Host ""
    Write-Host "Signing" -ForegroundColor Cyan

    $signArgs = @('-Tag', $tag, '-Thumbprint', $Thumbprint)
    if (-not $NoPublish) { $signArgs += '-Publish' }
    & $signScript @signArgs
    if ($LASTEXITCODE -ne 0) { throw "sign-release.ps1 failed." }
}
finally {
    Pop-Location
}

Write-Host ""
if ($NoPublish) {
    Write-Host "Done. $tag is still a draft -- publish it with:" -ForegroundColor Green
    Write-Host "  gh release edit $tag --draft=false"
} else {
    Write-Host "Done. tvdinner $Version is published and signed." -ForegroundColor Green
}
