# Code Signing Policy

tvdinner's Windows installer is signed with a code signing certificate
issued to the maintainer by [Certum](https://www.certum.eu/), held in
Certum's cloud HSM and used through their SimplySign service.

## What gets signed

Two files:

- `tvdinner.exe`, the application itself, built by
  [PyInstaller](https://pyinstaller.org) from
  [`windows/tvdinner.spec`](windows/tvdinner.spec).
- `tvdinner-setup-<version>.exe`, the installer that packages it, built by
  [Inno Setup](https://jrsoftware.org/isinfo.php) from
  [`windows/tvdinner.iss`](windows/tvdinner.iss).

The executable is signed **before** the installer is built, so the file
you run every day carries a signature and not just the one you ran once.

Nothing else is signed. In particular `libmpv-2.dll` and the bundled
Python runtime are third-party binaries: re-signing them would assert a
provenance the maintainer doesn't have. The `.deb` and `.rpm` are
unsigned too.

## Build and signing process

The build happens in GitHub Actions; the signature does not.

1. On a `v*` tag push, the Windows runner builds the frozen app with
   PyInstaller (`windows/tvdinner.spec`) and packages it into an
   installer with Inno Setup — see
   [`.github/workflows/release.yml`](.github/workflows/release.yml)'s
   `build-windows` job.
2. The release is created as a **draft**, carrying that bundle as a
   `.zip` alongside the `.deb` and `.rpm`. CI does not build the
   installer in this mode — it can't, because the executable has to be
   signed before Inno compresses it.
3. The maintainer runs
   [`windows/sign-release.ps1`](windows/sign-release.ps1) on a machine
   running SimplySign Desktop. It downloads the bundle, signs
   `tvdinner.exe`, builds the installer with Inno Setup, signs that too —
   both against Certum's timestamp authority, both verified — then
   uploads the installer, removes the bundle from the release, and
   publishes it.

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

## The certificate

| | |
|---|---|
| Subject | Open Source Developer Iain Smith |
| Issuer | Certum Code Signing 2021 CA |
| SHA-1 thumbprint | `6B58FE5ED40A67A23A27BEB25C4337ADEA26B9F9` |
| Valid until | 2027-09-04 |

Pass the thumbprint to the script as `-Thumbprint` so `signtool` selects by
`/sha1` rather than guessing with `/a`:

```powershell
.\windows\sign-release.ps1 -Tag v1.43.0 -Thumbprint 6B58FE5ED40A67A23A27BEB25C4337ADEA26B9F9 -Publish
```

After renewal, update the thumbprint here. The same certificate signs
[loadbearer](https://github.com/issinoho/loadbearer), whose
`scripts/sign-windows-release.ps1` takes the same argument.

First used for **1.43.0** (2026-09-04), signing `tvdinner.exe` and the
installer, both timestamped by `http://time.certum.pl`.

Note that `signtool verify` prints two different SHA-256 values: the
*Authenticode* hash, which covers the file minus its signature, and — from
this script's own `Signed SHA-256:` line — the hash of the file as
downloaded. winget manifests pin the latter.

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
