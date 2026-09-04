# Code Signing Policy

tvdinner's Windows installer is signed with a code signing certificate
issued to the maintainer by [Certum](https://www.certum.eu/), held in
Certum's cloud HSM and used through their SimplySign service.

## What gets signed

Only the Windows installer, `tvdinner-setup-<version>.exe`, produced by
[Inno Setup](https://jrsoftware.org/isinfo.php) from
[`windows/tvdinner.iss`](windows/tvdinner.iss). Nothing else in the
release (the `.deb`, the `.rpm`, or any file inside the installed
directory) is signed.

## Build and signing process

The build happens in GitHub Actions; the signature does not.

1. On a `v*` tag push, the Windows runner builds the frozen app with
   PyInstaller (`windows/tvdinner.spec`) and packages it into an
   installer with Inno Setup — see
   [`.github/workflows/release.yml`](.github/workflows/release.yml)'s
   `build-windows` job.
2. The release is created as a **draft**, carrying that installer along
   with the `.deb` and `.rpm`.
3. The maintainer runs
   [`windows/sign-release.ps1`](windows/sign-release.ps1) on a machine
   running SimplySign Desktop. It downloads the installer from the draft,
   signs it with `signtool` against Certum's timestamp authority,
   verifies the result, uploads the signed copy back, and publishes the
   release.

A release is therefore never public with an unsigned installer attached.

### Why signing isn't automated

Certum's SimplySign opens a signing session only after a one-time code
from its mobile app, and that session belongs to the machine it was
opened on. A GitHub-hosted runner is destroyed after every job and can
never hold one, so a hosted runner cannot sign.

The seed behind that one-time code could in principle be stored as a CI
secret to automate this. It isn't, deliberately: that would put the
second factor in the same place as the first, so anyone able to read the
repository's secrets could sign as the maintainer.

## Verifying a release

`signtool verify /pa /v tvdinner-setup-<version>.exe` on Windows, or
`osslsigncode verify` elsewhere. The signature should name the
maintainer and carry a timestamp from Certum, which keeps it valid after
the certificate itself expires.

Releases before **1.43.0** are unsigned.

## Roles

tvdinner is a [solo-maintained project](SECURITY.md). Iain Smith
(`iain@issinoho.com`, [@issinoho](https://github.com/issinoho)) holds
the certificate and performs every signing operation. There is no
separation of duties to describe, because there is no second maintainer
to separate them across.

## Contact

Questions about this policy, or a report that a signed tvdinner binary
doesn't match what this repository actually builds, go to
**iain@issinoho.com** or the process described in
[SECURITY.md](SECURITY.md).
