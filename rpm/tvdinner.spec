Name:           tvdinner
Version:        0.1.0
Release:        13%{?dist}
Summary:        IPTV player with M3U/XMLTV EPG integration

License:        Proprietary
URL:            https://github.com/issinoho/tvdinner
Source0:        %{name}-%{version}.tar.gz

# The automatic python dependency generator adds a versioned Requires
# for every entry in pyproject.toml's dependencies, scanned straight
# from the wheel metadata:
#  - python-mpv has no Fedora/RHEL RPM equivalent at any version, so
#    its Requires can never be satisfied on any Fedora system.
#  - pillow/requests do have real Fedora packages, but pyproject.toml's
#    floors (Pillow>=10, requests>=2.31) are just "whatever was current
#    when written", not a real API requirement (tvdinner only calls
#    long-stable Image/ImageDraw/ImageFont/ImageFilter/ImageOps and
#    requests.get APIs) -- so on older Fedora releases whose packaged
#    versions sit below those floors (e.g. Fedora 38: pillow 9.5,
#    requests 2.28), this generated Requires is stricter than
#    necessary and blocks an otherwise-fine install.
# Exclude all three; the manual, unversioned Requires below (mpv is
# still required by name; pillow/requests are satisfied by whatever
# version the distro ships) remain the real constraint.
%global __requires_exclude ^python3.*dist\\((python-mpv|pillow|requests)\\)

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  pyproject-rpm-macros

Requires:       mpv
Requires:       python3-pillow
Requires:       python3-requests

%description
tvdinner plays IPTV streams from M3U playlists using mpv, with a
TiviMate-style on-screen EPG overlay and a full program guide sourced
from XMLTV data (auto-discovered from the playlist, or an explicit
URL), including timezone-aware scheduling and a configurable
clock-correction shift for feeds with incorrect times.

Note: the python-mpv PyPI package (tvdinner's Python binding to mpv)
has no Fedora/RHEL RPM equivalent, so it is deliberately not listed as
a Requires here -- install it separately before running tvdinner, with:
    sudo pip install --prefix=/usr python-mpv
(add --break-system-packages if pip refuses with an "externally
managed environment" error). Two more-obvious-looking commands don't
work, both silently:
  - 'pip install --user ...': the installed /usr/bin/tvdinner script's
    shebang is '#!/usr/bin/python3 -sP', and -s specifically skips
    user site-packages.
  - plain 'sudo pip install ...' (no --prefix): on distros that
    redirect unmanaged pip installs away from dnf/rpm-owned
    directories, this lands in /usr/local/lib/pythonX.Y/site-packages,
    which some systems' system Python (e.g. Fedora 38) never searches
    at all -- --prefix=/usr installs directly into the dnf-owned
    site-packages tvdinner's own shebang actually searches.

%prep
%autosetup -n %{name}-%{version}

%build
%pyproject_wheel

%install
%pyproject_install
install -Dm644 debian/%{name}.1 %{buildroot}%{_mandir}/man1/%{name}.1

%files
%{_bindir}/%{name}
%{python3_sitelib}/%{name}/
%{python3_sitelib}/%{name}-%{version}*.dist-info/
%{_mandir}/man1/%{name}.1*
%doc README.md

%changelog
* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-13
- Speed up EPG startup: playback no longer blocks on EPG fetch/parse
  (loaded in a background thread and swapped in once ready), the
  on-disk cache now stores the parsed EPG alongside the raw bytes so a
  cache hit skips re-parsing too, and merge() only re-sorts schedules
  actually touched by the merged source

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-12
- Cache downloaded EPG data on disk (default: ~/.cache/tvdinner/epg),
  refreshed once a day by default, so startup with a large XMLTV feed
  doesn't re-download and re-parse it every time; a stale cache is
  used as a fallback if a refresh attempt fails. New --epg-cache-hours
  and --no-epg-cache flags control this

* Wed Jul 22 2026 Iain Smith <iain@issinoho.com> - 0.1.0-11
- Fix EPG data not matching for many real playlist/guide combinations:
  fall back to the tvg-id with a trailing '@SD'/'@HD'/etc. feed tag
  stripped (iptv-org's own playlists append one to disambiguate
  multiple feeds of one channel), then to a normalized display-name
  match (some XMLTV providers prefix every name with their own source
  tag, e.g. "PLUTO - 00s Replay"), before giving up

* Tue Jul 21 2026 Iain Smith <iain@issinoho.com> - 0.1.0-10
- Add key bindings for IR/BLE air-mouse remotes (e.g. nRF-based USB
  dongles): ENTER (their OK/center button) shows the EPG overlay
  outside the guide, and MENU toggles the full program guide

* Tue Jul 21 2026 Iain Smith <iain@issinoho.com> - 0.1.0-9
- Show a programme's release year (from XMLTV's <date> element) in
  the EPG banner, program guide timeline cells, and programme details
  popup, e.g. "The Lady From Shanghai (1948)"

* Mon Jul 20 2026 Iain Smith <iain@issinoho.com> - 0.1.0-8
- Fix Windows portability gaps: bundle the DejaVu fonts as package
  data instead of reading from an OS font directory (drops the
  dejavu-sans-fonts Requires, now redundant), use %%APPDATA%% for the
  EPG shift config path on Windows, and only apply the X11/Wayland
  gpu_context override on Linux -- it's a hard mpv option error, not a
  graceful no-op, on Windows builds of libmpv. Confirmed working
  end-to-end via a plain pip install on Windows.

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-7
- Correct the python-mpv install note again: plain 'sudo pip install'
  (no --user) isn't enough either -- it lands in
  /usr/local/lib/python3.11/site-packages, which this system's Python
  never searches (confirmed on Fedora 38). 'sudo pip install
  --prefix=/usr python-mpv' installs directly into the dnf-owned
  site-packages tvdinner's shebang actually searches, and is confirmed
  working end-to-end on a real Fedora 38 VM.

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-6
- Correct the python-mpv install note: the installed console-script's
  shebang is '#!/usr/bin/python3 -sP', and -s specifically excludes
  user site-packages, so 'pip install --user python-mpv' silently
  doesn't work -- needs a system-wide 'sudo pip install python-mpv'
  instead (found by actually testing an install on Fedora 38)

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-5
- Also exclude the auto-generated python3dist(pillow)/(requests)
  Requires, not just python-mpv -- their pyproject.toml version
  floors are stricter than tvdinner actually needs, and blocked
  install on Fedora 38 (ships pillow 9.5, requests 2.28) even though
  the code works fine with those versions

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-4
- Exclude the automatically-generated python3dist(python-mpv)
  Requires -- it's scanned straight from pyproject.toml's
  dependencies and can never be satisfied, since no Fedora/RHEL
  package provides python-mpv (install it separately via pip)

* Sun Jul 19 2026 Iain Smith <iain@issinoho.com> - 0.1.0-3
- Fix %%build/%%install to use %%pyproject_wheel/%%pyproject_install
  instead of %%py3_build/%%py3_install -- this project has no setup.py
  (pyproject.toml/PEP 517 only), so the legacy macros' implicit
  'python3 setup.py build' failed with ENOENT

* Sat Jul 18 2026 Iain Smith <iain@issinoho.com> - 0.1.0-2
- Add -v/--version flag to report the tvdinner package version

* Sat Jul 18 2026 Iain Smith <iain@issinoho.com> - 0.1.0-1
- Initial RPM packaging, tracking the .deb package's feature set:
  M3U playback via mpv, XMLTV EPG overlay and full program guide with
  channel-name filtering, per-channel EPG time-shift correction
  (config file and live keybinding), aspect ratio cycling.
