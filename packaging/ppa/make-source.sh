#!/usr/bin/env bash
#
# Build signed source packages for ppa:issinoho/tvdinner, one per Ubuntu series.
#
# Launchpad builds the binaries itself from a *source* package, so this script
# exports a git ref as tvdinner_<version>.orig.tar.gz and then, for each series,
# unpacks that same tarball, drops debian/ back in with a series-suffixed
# version, and runs dpkg-buildpackage -S over it.
#
# The orig tarball is built once and reused for every series on purpose:
# Launchpad keys it by name and rejects a second upload of the same filename
# with different bytes, so all the series uploads for one release must share it
# exactly.
#
# Unlike the sibling Rust projects there is nothing to vendor -- every runtime
# dependency is an archive package named in debian/control, so the builders
# having no network costs us nothing.
#
# See packaging/ppa/README.md for the surrounding procedure.

set -euo pipefail

REF="HEAD"
SERIES="noble,resolute"
PPA_BUILD=1
KEY=""
OUTDIR=""

usage() {
	cat <<EOF
Usage: $0 [options]

  -r, --ref REF        git ref to package (default: HEAD)
  -s, --series LIST    comma-separated Ubuntu series (default: $SERIES)
  -n, --ppa-build N    PPA build number within the series (default: 1);
                       bump it to re-upload the same release to the same series
  -k, --key KEYID      GPG key to sign with; without it the packages are
                       unsigned and Launchpad will not accept them
  -o, --output DIR     where to write (default: <repo>/../ppa-tvdinner-<version>)
  -h, --help           this

Example:
  $0 --ref v1.43.0 --key 0xDEADBEEF
EOF
}

while [ $# -gt 0 ]; do
	case "$1" in
		-r|--ref) REF="$2"; shift 2 ;;
		-s|--series) SERIES="$2"; shift 2 ;;
		-n|--ppa-build) PPA_BUILD="$2"; shift 2 ;;
		-k|--key) KEY="$2"; shift 2 ;;
		-o|--output) OUTDIR="$2"; shift 2 ;;
		-h|--help) usage; exit 0 ;;
		*) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
	esac
done

for tool in git dpkg-buildpackage dpkg-source tar; do
	command -v "$tool" >/dev/null || {
		echo "error: $tool is not installed (dpkg-* come from dpkg-dev)" >&2
		exit 1
	}
done

REPO="$(git rev-parse --show-toplevel)"
cd "$REPO"

git rev-parse --verify --quiet "$REF^{commit}" >/dev/null || {
	echo "error: '$REF' is not a commit in this repository" >&2
	exit 1
}

# Read every version out of the ref rather than the working tree, so that
# packaging an old tag from a dirty checkout still describes that tag.
VERSION="$(git show "$REF:pyproject.toml" | sed -n 's/^version *= *"\(.*\)"/\1/p' | head -n1)"
INIT_VERSION="$(git show "$REF:src/tvdinner/__init__.py" | sed -n 's/^__version__ *= *"\(.*\)"/\1/p' | head -n1)"
DEB_VERSION="$(git show "$REF:debian/changelog" | sed -n '1s/^[^(]*(\([^)]*\)).*/\1/p')"

[ -n "$VERSION" ] || { echo "error: no version in pyproject.toml at $REF" >&2; exit 1; }

# A release bump touches five files together (see CLAUDE.md, "Release
# versioning"). These are the three that decide what a built package calls
# itself, so a half-finished bump should stop here rather than upload a
# package labelled with the previous release.
if [ "$VERSION" != "$INIT_VERSION" ]; then
	echo "error: pyproject.toml is at $VERSION but src/tvdinner/__init__.py is at $INIT_VERSION at ref $REF" >&2
	exit 1
fi
if [ "$VERSION" != "$DEB_VERSION" ]; then
	echo "error: pyproject.toml is at $VERSION but debian/changelog is at $DEB_VERSION at ref $REF" >&2
	echo "       add a changelog entry for $VERSION (see CLAUDE.md, 'Release versioning')" >&2
	exit 1
fi

[ -n "$OUTDIR" ] || OUTDIR="$REPO/../ppa-tvdinner-$VERSION"
mkdir -p "$OUTDIR"
OUTDIR="$(cd "$OUTDIR" && pwd)"

ORIG="$OUTDIR/tvdinner_${VERSION}.orig.tar.gz"
SRCDIR="$OUTDIR/tvdinner-$VERSION"

echo "==> tvdinner $VERSION from $REF -> $OUTDIR"

# ---------------------------------------------------------------- orig tarball

rm -rf "$SRCDIR"
mkdir -p "$SRCDIR"
git archive --format=tar "$REF" | tar -x -C "$SRCDIR"

# debian/ belongs to the .debian.tar.xz, not to the upstream tarball; it goes
# back in per-series below, with the series-suffixed version.
rm -rf "$SRCDIR/debian"

echo "==> writing $(basename "$ORIG")"
# Fixed ownership and a mtime taken from the commit, so that regenerating the
# tarball from the same ref gives the same bytes -- Launchpad compares them.
COMMIT_TS="$(git log -1 --format=%ct "$REF")"
rm -f "$ORIG"
tar --sort=name --owner=0 --group=0 --numeric-owner \
	--mtime="@$COMMIT_TS" \
	-czf "$ORIG" -C "$OUTDIR" "tvdinner-$VERSION"
ls -l "$ORIG" | awk '{printf "    %.1f MB\n", $5/1048576}'

# ------------------------------------------------------------ source packages

if [ -n "$KEY" ]; then
	# dpkg-buildpackage warns on anything shorter than a fingerprint, so accept
	# a short id, long id or email and hand it the fingerprint regardless.
	FPR="$(gpg --with-colons --list-keys "$KEY" 2>/dev/null | awk -F: '$1=="fpr"{print $10; exit}')"
	if [ -z "$FPR" ]; then
		echo "error: no GPG key in your keyring matches '$KEY'" >&2
		exit 1
	fi
	echo "==> signing with $FPR"
	SIGN_ARGS=(-k"$FPR")
else
	SIGN_ARGS=(-us -uc)
	echo "==> WARNING: no --key given; the source packages will be unsigned"
	echo "    and Launchpad will reject them. This is a dry run."
fi

CHANGES=()
IFS=',' read -r -a SERIES_LIST <<< "$SERIES"
for series in "${SERIES_LIST[@]}"; do
	echo "==> building source package for $series"
	rm -rf "$SRCDIR"
	tar -xzf "$ORIG" -C "$OUTDIR"
	git archive --format=tar "$REF" debian | tar -x -C "$SRCDIR"

	# The repo packages tvdinner as a *native* package: one version, no Debian
	# revision, which is what the .deb attached to each GitHub release is built
	# from. A PPA needs the opposite -- a revision to hang the series suffix
	# off, so that the same upstream release can be uploaded to noble and to
	# resolute as two different versions. Rewrite the format here rather than
	# in the repo, so CI's binary-only build is left exactly as it was.
	echo "3.0 (quilt)" > "$SRCDIR/debian/source/format"

	# 1.43.0-1~noble1 sorts below the plain 1.43.0-1, so a user who later gets
	# the package from the archive proper is upgraded rather than held back.
	sed -i "1s/^tvdinner ([^)]*) [^;]*;/tvdinner (${VERSION}-1~${series}${PPA_BUILD}) ${series};/" \
		"$SRCDIR/debian/changelog"
	head -n1 "$SRCDIR/debian/changelog" | sed 's/^/    /'

	# -d skips the build-dependency check: a source-only build compiles
	# nothing, so there is no reason for this machine to have the build-deps.
	( cd "$SRCDIR" && dpkg-buildpackage -S -sa -d "${SIGN_ARGS[@]}" ) >/dev/null
	CHANGES+=("$OUTDIR/tvdinner_${VERSION}-1~${series}${PPA_BUILD}_source.changes")
done

rm -rf "$SRCDIR"

if command -v lintian >/dev/null; then
	echo "==> lintian"
	for c in "${CHANGES[@]}"; do
		echo "    $(basename "$c")"
		# Warnings are expected (a PPA upload closes no bug, and the changelog
		# entries are the upstream ones rather than Debian-style); only errors
		# should stop an upload.
		lintian --fail-on error "$c" || {
			echo "error: lintian found errors in $(basename "$c") -- not printing upload commands" >&2
			exit 1
		}
	done
else
	echo "==> lintian not installed, skipping the check"
fi

echo
# ssh-ppa, not ppa: Launchpad retired anonymous FTP upload, and the ppa
# profile still points at it -- it hangs, then blames the network.
echo "Done. Upload with:"
for c in "${CHANGES[@]}"; do
	echo "  dput ssh-ppa:issinoho/tvdinner $c"
done
