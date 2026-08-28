## What changed and why

<!-- A short description. Link a related issue if there is one (e.g. "Fixes #123"). -->

## How was this tested?

<!--
- `pytest` output (or just "pytest passes locally" if nothing unusual).
- If this touches cli.py's interactive closures (a browser, a keybinding,
  anything nested inside play_stream()) -- those are deliberately not
  unit-tested (see CONTRIBUTING.md/CLAUDE.md); describe how you exercised
  it live instead.
-->

## Checklist

- [ ] `pytest` passes locally
- [ ] New/changed behavior has test coverage, where the project's
      [testing conventions](CONTRIBUTING.md#testing-conventions) call
      for it
- [ ] Docs updated if this changes user-facing behavior (README, and/or
      the relevant [wiki](https://github.com/issinoho/tvdinner/wiki)
      page)

<!--
No need to touch version numbers, CHANGELOG.md, or the packaging files
(debian/changelog, rpm/tvdinner.spec) -- that's a maintainer step done
at release time.
-->
