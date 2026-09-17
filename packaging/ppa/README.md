# Publishing tvdinner to a Launchpad PPA

The `.deb` attached to each GitHub release is a one-off download. A PPA is the
version people actually want: `apt` knows about it, so upgrades arrive with
everything else on the machine.

tvdinner is published to
[`ppa:issinoho/tvdinner`](https://launchpad.net/~issinoho/+archive/ubuntu/tvdinner).
`debian/` is shared with the release `.deb`; the only thing a PPA needs on top
is a *source* package that a Launchpad builder can build, which is what
`make-source.sh` in this directory produces.

Unlike the sibling Rust projects there is nothing to vendor. Every runtime
dependency is an archive package named in `debian/control`, so the builders
having no network costs us nothing.

## Which series, and why those

tvdinner's floors are `python-mpv>=1.0.7`, `Pillow>=10.0`, `requests>=2.31`
and Python 3.10, which is what decides this:

| Series | python3-mpv | python3-pil | python3-requests | python3 | Verdict |
|---|---|---|---|---|---|
| **resolute** 26.04 LTS | 1.0.8 | 12.1.1 | 2.32.5 | 3.14 | builds as-is |
| **noble** 24.04 LTS | 1.0.4 | 10.2.0 | 2.31.0 | 3.12 | builds; see below |
| jammy 22.04 LTS | 0.5.2 | 9.0.1 | 2.25.1 | 3.10 | ruled out |

**noble sits just below the stated python-mpv floor** — 1.0.4 against a
`>=1.0.7` in `pyproject.toml` — and ships anyway, because that floor was
written in the initial commit as whatever was current at the time and never
derived from a feature. It was checked rather than assumed: every API
`player.py` touches (`on_key_press`, `unregister_key_binding`,
`observe_property`, `event_callback`, `key_binding`, `wait_for_playback`,
`show_text`, `loadfile`, `seek`, `terminate`, `play`, `command`, plus
`ErrorCode`/`ShutdownError`/`MpvEventEndFile`) exists in 1.0.4, and the full
suite passes against it — 1244 passed. Properties go through `__getattr__` to
libmpv, so they depend on noble's mpv, not on the wrapper. `debian/control`
depends on `python3-mpv` unversioned, so nothing enforces the pyproject floor
here. **Re-check this if the floor ever becomes real**, i.e. if something
starts using a wrapper API newer than 1.0.4.

**jammy is ruled out on three floors at once**, not just one, and its
python-mpv 0.5.2 cannot even import against a modern libmpv — it binds
`mpv_detach_destroy`, which libmpv 2 removed. Supporting it would mean real
compatibility shims, not a packaging entry.

## One-time setup

1. **A GPG key registered with Launchpad.** Launchpad validates an upload
   signature against the keys on the *account*, not per-PPA, so any registered
   key can upload here — but tvdinner follows the sibling projects in having
   its own, so a compromise is scoped to one project.

   The key needs an encryption subkey, not just a signing one: Launchpad
   confirms registration by emailing a token **encrypted to the key**, and a
   sign-only key cannot complete the step.

   ```
   gpg --quick-generate-key "tvdinner releases (PPA signing key for issinoho/tvdinner) <iain@issinoho.com>" rsa4096 sign 2y
   gpg --quick-add-key <fingerprint> rsa4096 encr 2y
   ```

   Launchpad fetches the key from a keyserver rather than taking it inline, so
   publish it before registering it. **`gpg --send-keys` does not work from
   this machine** — it fails with `keyserver send failed: Server indicated a
   failure` over both `hkp://` and `hkps://`, while a direct submission of the
   same key to the same host succeeds. Both ports are reachable and there is no
   proxy or `dirmngr.conf` involved, so this is dirmngr's submission path
   rather than the network. POST the key yourself instead:

   ```
   gpg --armor --export <fingerprint> > key.asc
   curl --data-urlencode "keytext@key.asc" https://keyserver.ubuntu.com/pks/add
   ```

   A `{"inserted":[...]}` response means it landed.

   Don't be alarmed if a lookup then can't find it. keyserver.ubuntu.com
   answers from several replicas that disagree for a long time after a
   submission — the same query returned `info:1:1` from one node and
   `Not Found` from the next, still splitting roughly half and half well after
   the insert. **This did not stop Launchpad**, which fetched the key and sent
   its confirmation mail while the split was ongoing, so there is no need to
   wait for the replicas to converge before registering. Treat a failed lookup
   as noise, and a failed *registration* as the only real signal:

   ```
   curl -sS "https://keyserver.ubuntu.com/pks/lookup?op=index&options=mr&search=0x<fingerprint>"
   ```

   Then paste the fingerprint at <https://launchpad.net/~/+editpgpkeys>. The
   mail goes to the address in the key's UID; decrypt it and follow the link.
   Not instant, so do it before you need it.

   The tvdinner key is `0B83F05D97D64C61139FC83C7A734F088E5AB20A`, created
   2026-09-17 and expiring 2028-09-16.

2. **The PPA.** Already created — `ppa:issinoho/tvdinner`. Under *Change
   details* → *Processors*, note that every enabled processor builds every
   series, so the architecture list decides how much work an upload makes.
   tvdinner is `Architecture: all`, so one build per series covers every
   architecture regardless.

3. **An SSH key registered with Launchpad**, at
   <https://launchpad.net/~/+editsshkeys>. Not optional: Launchpad retired
   anonymous FTP uploads, so `ppa.launchpad.net:21` accepts a TCP connection
   but never sends a banner — `dput ppa:...` hangs and then reports
   `Connection failed, aborting. Check your network`, which is misleading.
   Uploads go over SFTP, authenticated by this key.

   To check it: `ssh -T issinoho@ppa.launchpad.net` should answer
   `No shells on this server.` — that is a successful authentication.

4. **Local tools**:

   ```
   sudo apt install dpkg-dev dput lintian
   ```

   `dput` ships an `ssh-ppa` profile that is correct except for `login = *`,
   which it resolves to `$USER` — the local account name, not the Launchpad
   one. Override it once in `~/.dput.cf` (already done on this machine):

   ```
   [ssh-ppa]
   login = issinoho
   ```

## Per release

After the tag is pushed and CI has published the GitHub release:

```
packaging/ppa/make-source.sh --ref v1.43.0 --key <key>
```

`--key` takes any spelling gpg understands — short id, long id, fingerprint or
email — and the script resolves it to a fingerprint before handing it on,
because `dpkg-buildpackage` warns about anything shorter.

That writes to `../ppa-tvdinner-1.43.0/` and finishes by printing the upload
commands:

```
dput ssh-ppa:issinoho/tvdinner ../ppa-tvdinner-1.43.0/tvdinner_1.43.0-1~noble1_source.changes
dput ssh-ppa:issinoho/tvdinner ../ppa-tvdinner-1.43.0/tvdinner_1.43.0-1~resolute1_source.changes
```

Note `ssh-ppa:`, not `ppa:` — the latter is the dead FTP path.

Launchpad emails an acceptance or rejection within a minute or two, then queues
the builds; watch them at
<https://launchpad.net/~issinoho/+archive/ubuntu/tvdinner/+packages>.

A build reaching *Successfully built* does **not** mean anyone can install it.
The binaries sit at `Pending` until Launchpad's publisher next runs, which is
what actually writes `dists/<series>/main/binary-all/Packages.gz`. Until then
`apt` still offers the previous version. To check whether a release is
genuinely live, read the index rather than the build page — and rather than the
API, whose `getPublishedBinaries` is served from replicas that disagree with
each other:

```
curl -sfL https://ppa.launchpadcontent.net/issinoho/tvdinner/ubuntu/dists/noble/main/binary-amd64/Packages.gz \
  | gunzip -c | awk '/^Version:/{print $2}' | sort -u
```

(The uncompressed `Packages` is a 404 — only the compressed index is served.)

Omit `--key` for a dry run: everything is built unsigned, which is enough to
check that the source package assembles and passes lintian, but Launchpad will
not accept the result.

## What the script does, and why

- **Exports a git ref**, not the working tree, so packaging an old tag from a
  dirty checkout still describes that tag. All three versions are read out of
  the ref, and it refuses to continue unless `pyproject.toml`,
  `src/tvdinner/__init__.py` and `debian/changelog` agree — a half-finished
  release bump should stop here rather than upload a package labelled with the
  previous release.

- **Rewrites `debian/source/format` to `3.0 (quilt)`** in the exported tree,
  leaving the repo's own packaging alone. The repo builds tvdinner as a
  **native** package — one version, no Debian revision — which is what the
  release `.deb` is built from. A PPA needs the opposite: a revision to hang
  the series suffix off, so one upstream release can go to noble and resolute
  as two different versions. Doing it here rather than in the repo keeps CI's
  binary-only `dpkg-buildpackage -b` exactly as it was, since a binary build
  never produces a source package and so never reads the format meaningfully.

- **Builds one orig tarball and reuses it for every series.** Launchpad keys
  the tarball by filename and rejects a second upload of the same name with
  different bytes, so all the series uploads for one release must share it
  exactly. The tarball is written with fixed ownership and the commit's
  timestamp so that regenerating it from the same ref gives the same bytes.

- **Versions per series** as `1.43.0-1~noble1`. The `~` sorts *below* the plain
  `1.43.0-1`, so anyone who later gets the package from the Ubuntu archive
  proper is upgraded onto it rather than held back on the PPA copy.
  Re-uploading the same release to the same series needs a fresh version: pass
  `--ppa-build 2`.

## Gotchas

- **Launchpad accepts a given version once.** A rejected build cannot be fixed
  by re-uploading the same version — bump `--ppa-build`.
- **Source-only uploads.** Launchpad builds the binaries itself; never upload a
  `.deb`.
- **gpg prompts once per run.** Dismiss or time out the passphrase prompt and
  the build dies at `signfile` with `gpg: signing failed: Operation cancelled`.
  Just run it again.
- **lintian warns about the historical changelog versions.**
  `odd-historical-debian-changelog-version 1.42.1 (for non-native)` is expected
  and harmless: every entry below the top one is a native version, because the
  repo's packaging is native and only the top entry gets rewritten. It is a
  warning, not an error, so it does not stop an upload.
