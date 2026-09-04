# winget

Getting tvdinner into the [Windows Package Manager][winget] so
`winget install Issinoho.tvdinner` works.

## Where things stand

The manifests in `1.42.1/` are the **initial submission** — validated against
the official 1.12.0 schemas, with the installer URL and SHA-256 checked against
the real published asset. They are a snapshot, not a maintained copy:
`microsoft/winget-pkgs` is canonical once the package is accepted, and
`.github/workflows/winget.yml` opens the pull request for every release after
this one.

## Two things to settle first

**Releases up to 1.42.1 are unsigned.** From 1.43.0 the installer is signed
with a Certum certificate — see [`CODE_SIGNING_POLICY.md`](../../CODE_SIGNING_POLICY.md).

That matters here because **a manifest pins the installer's SHA-256**. The
manifests in `1.42.1/` describe the unsigned asset, which is what the
[first submission](https://github.com/microsoft/winget-pkgs/pull/429454) is
reviewing. Don't re-sign 1.42.1 retroactively: it would change the hash out
from under a manifest already under review. Signing starts with the next
release, and the auto-submit workflow reads the hash from the published
asset, so it picks up the signed one by itself.

**PyInstaller bundles get flagged.** The installer wraps a PyInstaller
onedir build, which antivirus heuristics dislike — a self-extracting archive
full of compiled Python and a bundled mpv looks a lot like packed malware.
winget's validation pipeline runs Defender against every submission, and
`Validation-Defender-Error` is a realistic outcome. It's usually resolved by a
maintainer re-running validation or a false-positive report to Microsoft, but
it can stall the first submission. Signing reduces the odds; it doesn't
eliminate them.

Neither blocks a submission. Both are worth knowing before one is made.

## Submitting the first version

1. Fork [`microsoft/winget-pkgs`][winget-pkgs].
2. Copy this directory's manifests to
   `manifests/i/Issinoho/tvdinner/1.42.1/` in the fork.
3. Commit, push, and open a pull request against `microsoft/winget-pkgs`.
   The automated validation builds a VM, installs the package silently,
   checks it appears in Apps & Features under the declared `ProductCode`,
   and uninstalls it.

Nothing here submits that pull request for you: it is a change to someone
else's repository, published under your name.

## Keeping it current

Set two things on this repository, and every subsequent release submits itself:

| Kind | Name | Value |
| --- | --- | --- |
| Variable | `WINGET_ENABLED` | `true` |
| Secret | `WINGET_TOKEN` | a classic PAT with the `public_repo` scope |

The workflow is skipped entirely while `WINGET_ENABLED` is unset, so it costs
nothing until the first submission has been accepted.

## Why these values

| Field | Value | Where it comes from |
| --- | --- | --- |
| `PackageIdentifier` | `Issinoho.tvdinner` | GitHub org, then the product's own lower-case styling. No collision in winget-pkgs. |
| `ProductCode` | `{7B591A96-…}_is1` | Inno appends `_is1` to the `AppId` in `windows/tvdinner.iss`. This is how winget tracks upgrades, so it must not change. |
| `InstallerType` | `inno` | winget then knows the silent switches; none need declaring. |
| `Scope` | `machine` | `DefaultDirName={autopf}` installs to Program Files. |
| `MinimumOSVersion` | `10.0.0.0` | The build targets x64 Windows 10+. |

[winget]: https://learn.microsoft.com/windows/package-manager/
[winget-pkgs]: https://github.com/microsoft/winget-pkgs
