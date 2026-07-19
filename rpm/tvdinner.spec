Name:           tvdinner
Version:        0.1.0
Release:        4%{?dist}
Summary:        IPTV player with M3U/XMLTV EPG integration

License:        Proprietary
URL:            https://github.com/issinoho/tvdinner
Source0:        %{name}-%{version}.tar.gz

# python-mpv (tvdinner's binding to mpv) has no Fedora/RHEL RPM
# equivalent (see the note in %description), but the automatic
# python dependency generator adds a Requires for it anyway, scanned
# straight from pyproject.toml's dependencies -- which can never be
# satisfied on any Fedora system. Suppress just that one; the
# generator's pillow/requests Requires are left alone since those do
# have real Fedora packages.
%global __requires_exclude ^python3.*dist\\(python-mpv\\)$

BuildArch:      noarch
BuildRequires:  python3-devel
BuildRequires:  python3-setuptools
BuildRequires:  python3-pip
BuildRequires:  python3-wheel
BuildRequires:  pyproject-rpm-macros

Requires:       mpv
Requires:       python3-pillow
Requires:       python3-requests
Requires:       dejavu-sans-fonts

%description
tvdinner plays IPTV streams from M3U playlists using mpv, with a
TiviMate-style on-screen EPG overlay and a full program guide sourced
from XMLTV data (auto-discovered from the playlist, or an explicit
URL), including timezone-aware scheduling and a configurable
clock-correction shift for feeds with incorrect times.

Note: the python-mpv PyPI package (tvdinner's Python binding to mpv)
has no Fedora/RHEL RPM equivalent, so it is deliberately not listed as
a Requires here -- install it separately, e.g. with
'pip install --user python-mpv', before running tvdinner.

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
