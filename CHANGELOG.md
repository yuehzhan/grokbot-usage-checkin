# Changelog

All notable changes to this project are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/):
MAJOR for changes that need you to do something, MINOR for new features, PATCH for fixes only.
The current version is in the `VERSION` file and shown small at the top of the page.
A Traditional Chinese version is in [CHANGELOG.zh-TW.md](CHANGELOG.zh-TW.md).

## [1.1.0] - 2026-10-25

### Added
- Gauge rev-up animation with a menu: Off / Once / Continuous.
- Display offset menu gives instant feedback ("Applying…") while the charts are redrawn.
- Stat cards carry small tags that say what each number is.
- GitHub icon link at the end of the toolbar (opens the project page).
- "What's new": when Check for updates finds a newer version, the page lists the CHANGELOG entries since your
  version before you press Update.
- "Roll back to previous version": the version you had before the last update is saved, and one click (with a
  confirmation) goes back to it. Your data and settings are kept, and later updates work as usual.
- Version number (`VERSION`) shown at the top of the page; this CHANGELOG.
- README: animated GIF and screenshots made with sample data.

### Changed
- Projections above 100% now say plainly that the quota runs out early and about how many days are left.
- Days-left wording: the budget card says days until the reset; the projection card says about how many days
  of quota are left.
- Chart labels cleaned up: the floating baseline label on the line chart is gone (the legend and footer still show
  the baseline).
- README privacy wording is accurate: everything stays on your computer except the optional update check
  (`git fetch` from GitHub, about once a day, can be turned off).

### Fixed
- Toolbar no longer stacks one character per line in narrow windows: two tidy rows (settings / actions) at
  1024 px and wider, wrapping gracefully below.
- English gauge animation: the moving number is fully covered so no old digits show through.

## [1.0.0] - 2026-10-24

First tagged state (everything before version numbers were added): usage history charts and clock view,
check-ins, palettes (including colour-blind-safe ones and patterns), language and time zone menus, display offset,
desktop notifications, check for updates and one-click update, Windows and Linux service scripts.

[1.1.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#110---2026-10-25
[1.0.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#100---2026-10-24
