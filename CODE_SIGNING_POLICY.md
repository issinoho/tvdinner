# Code Signing Policy

tvdinner's Windows installer is signed using a free code-signing
certificate provided by the [SignPath Foundation](https://signpath.org)
for open source projects. This document exists to satisfy SignPath
Foundation's requirement that participating projects publish their
code signing policy.

## What gets signed

Only the Windows installer, `tvdinner-setup-<version>.exe`, produced by
[Inno Setup](https://jrsoftware.org/isinfo.php) from
[`windows/tvdinner.iss`](windows/tvdinner.iss). Nothing else in the
release (the `.deb`, the `.rpm`, or any file inside the installed
directory) is signed.

## Build and signing process

Every release is built and signed by GitHub Actions, not on a
maintainer's own machine — see
[`.github/workflows/release.yml`](.github/workflows/release.yml)'s
`build-windows` job. On a `v*` tag push (or a manual run with
publishing enabled):

1. The Windows runner builds the frozen app with PyInstaller
   (`windows/tvdinner.spec`) and packages it into an unsigned installer
   with Inno Setup.
2. The unsigned installer is uploaded as a GitHub Actions build
   artifact.
3. SignPath's GitHub connector verifies the artifact was produced by
   this exact workflow, from this repository, before accepting a
   signing request for it — see
   [SignPath's GitHub trusted build system docs](https://docs.signpath.io/trusted-build-systems/github).
4. The signed installer is downloaded back into the workflow and is
   the file actually attached to the GitHub Release.

A manual `workflow_dispatch` test run with publishing left disabled
does **not** submit a signing request — only a real release does.

## Roles

tvdinner is a [solo-maintained project](SECURITY.md). Iain Smith
(`iain@issinoho.com`, [@issinoho](https://github.com/issinoho)) holds
every SignPath role — Author, Reviewer, and Approver — for lack of
another maintainer to separate them across. Every signing request
originates from the `release.yml` workflow described above, not from a
manually triggered signing outside of CI.

## Contact

Questions about this policy, or a report that a signed tvdinner binary
doesn't match what this repository actually builds, go to
**iain@issinoho.com** or the process described in
[SECURITY.md](SECURITY.md).
