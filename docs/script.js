// Populates the download buttons from the actual latest GitHub release,
// so this page never has a stale/hardcoded version or asset filename
// baked in (release asset names include the version, which changes
// every release). Buttons already have a sensible fallback href (the
// releases page itself) in the HTML, in case this fetch fails or JS is
// disabled.
(function () {
  const REPO = "issinoho/tvdinner";

  function setDownload(selector, url) {
    if (!url) return;
    document.querySelectorAll(selector).forEach((el) => {
      el.href = url;
    });
  }

  function setVersionText(version) {
    document.querySelectorAll(".release-version").forEach((el) => {
      el.textContent = version;
    });
  }

  fetch(`https://api.github.com/repos/${REPO}/releases/latest`)
    .then((response) => {
      if (!response.ok) throw new Error(`GitHub API returned ${response.status}`);
      return response.json();
    })
    .then((release) => {
      const assets = release.assets || [];
      const version = release.tag_name || "";
      const find = (suffix) => assets.find((a) => a.name.toLowerCase().endsWith(suffix));

      const exe = find(".exe");
      const dmg = find(".dmg");
      const deb = find(".deb");
      const rpm = find(".rpm");

      setDownload('[data-download="windows"]', exe && exe.browser_download_url);
      setDownload('[data-download="dmg"]', dmg && dmg.browser_download_url);
      setDownload('[data-download="deb"]', deb && deb.browser_download_url);
      setDownload('[data-download="rpm"]', rpm && rpm.browser_download_url);

      const heroBtn = document.getElementById("download-windows-hero");
      if (heroBtn && exe) heroBtn.textContent = `Download for Windows (${version})`;

      setVersionText(version);
    })
    .catch((err) => {
      // Leave the fallback releases-page links in place -- still useful,
      // just not a direct link to a specific asset.
      console.warn("Could not fetch the latest tvdinner release:", err);
    });
})();
