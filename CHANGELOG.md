# Changelog

All notable changes to this project are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/):
MAJOR for changes that need you to do something, MINOR for new features, PATCH for fixes only.
The current version is in the `VERSION` file and shown small at the top of the page.
The changelog is kept in English only; [CHANGELOG.zh-TW.md](CHANGELOG.zh-TW.md) is no longer updated.

## [1.2.1] - 2026-10-25

### Added
- A small easter egg: hover the version tag next to the title.

### Changed
- Update notes ("What's new") are always shown in English from CHANGELOG.md, whatever the page language; the panel
  heading is still translated. The changelog is maintained in English only; CHANGELOG.zh-TW.md is no longer updated.

### Removed
- The translucent glow block on the ring's leading edge in Continuous animation mode; the edge keeps its small jitter.

## [1.2.0] - 2026-10-25

### Added
- Solid-colour palettes in their own "Solid colours" section of the Colours menu, below the ★ note, with
  `teal-mono` (All teal) first, then `pink-mono`, `purple-mono`, `blue-mono` and `green-mono`. Over is the bright
  light shade, under the deep muted shade; ▲ / ▼ and patterns still apply.
- Custom colours: "Custom…", the last item in the Colours menu, shows two colour pickers (under / over baseline),
  remembered in the browser; a hint appears when the two colours are too similar. Also `"palette": "custom"` with
  `"custom_colors"` in the config, for the PNG renderer and `--palette custom`.
- README: check-in search demo GIF.

### Changed
- Colours menu order: the ★ presets form one block at the end of the two-colour section, right above the ★ note.
- `amber-purple` is now labelled "Golden orange / purple" (zh-TW 金橘／紫); the config key is unchanged.
- README screenshots and the gauge GIF were re-made with sample data that has days both above and below the
  baseline, so both colours show.
- "Roll back to previous version" sits right after "Check for updates" in the toolbar, kept together with it, instead
  of on a line of its own below the toolbar. Likewise the GitHub icon always stays right after "Auto-check for updates".

### Removed
- `high-contrast` is no longer in the Colours menu. It stays a legacy key: configs, theme files and `--palette` still
  accept and draw it (`yellow-deepsky` too); a browser that had it saved falls back to the configured palette.

### Fixed
- Run-out projection: when the quota is projected to run out before the reset, the card is titled "Projected run-out"
  instead of "Projected (7d)", and the chart's dashed line uses the same rate as the card, crossing the 100% line
  where the card says (with its marker and label there) instead of drifting to day 7. Under one day left is shown
  in hours (e.g. "about 8 h").
- Rates and averages (average per day, burn rate, daily budget, chart labels) show one decimal only when it is not a
  whole number: 74% instead of 74.0%, 10.8% stays, matching the whole-number cards beside them.

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
- Credits section in both READMEs.

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

[1.2.1]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#121---2026-10-25
[1.2.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#120---2026-10-25
[1.1.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#110---2026-10-25
[1.0.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#100---2026-10-24
