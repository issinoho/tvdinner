"""HTTP defaults for IPTV provider requests."""

# Some providers reset connections or return misleading HTTP errors for
# python-requests' default user agent, even with valid credentials. A browser
# user agent lets their Xtream API, playlist and guide endpoints respond.
PROVIDER_USER_AGENT = "Mozilla/5.0"
