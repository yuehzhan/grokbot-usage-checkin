# Changelog

All notable changes to this project are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/):
MAJOR for changes that need you to do something, MINOR for new features, PATCH for fixes only.
The current version is in the `VERSION` file and shown small at the top of the page.
The changelog is kept in English only; [CHANGELOG.zh-TW.md](CHANGELOG.zh-TW.md) is no longer updated.

## [1.6.0] - 2026-10-28

### Added
- **Daily** tab (每日增量) can switch between **per day** and **per hour**. Per hour draws only real readings (never
  interpolated), marks gaps with hatching, and shows a "not enough readings" note for light users. Per hour opens by
  default once the week has 12 or more hourly readings.
- Usage clock **Hours** menu (left of Range, applies to both dials): **Last 24 h** (default, drawn exactly as in
  1.5.1), **Last 12 h** (AM before 12:00 local time, PM from 12:00, decided again on every refresh), **AM 12 h**
  and **PM 12 h** (a 12-hour clock face with 12 at the top). The other half is left out; peak, average, interval
  count and check-in dots cover only the shown half. An AM / PM label sits in a small hub at the dial centre.
- The 12-hour views have their own titles, a subtitle that gives the real span (for example
  `10/27 02:37–12:00 + 10/28 00:00–02:37`) and an explanatory line under the toolbar, in all interface languages.
- The page remembers the last main tab and the per-day / per-hour choice in the browser (`localStorage`).

### Changed
- Header tabs are now **Week** (本週總覽, the default) and **Daily** (每日增量); the Cumulative tab is removed.
- Clock tab: the toolbar sits above both cards so they top-align.

### Fixed
- A page that was loaded before a server restart (stale per-start token, `403` from `/api/palette/render`) now keeps
  the menu choice and reloads once instead of snapping back to the default.

Language batch 2 (Italian, Polish, Turkish, Vietnamese, Malay) is not part of this release; it moves to a later version.

## [1.5.1] - 2026-10-27

### Changed
- **Language** menu: the numeric option is now shown as just `123456` in every language and sits at the very
  bottom, after all languages (new languages are added above it).

## [1.5.0] - 2026-10-27

### Added
- Five new interface languages: **Español** (`es`), **Português** (`pt`, Brazilian-neutral), **Français** (`fr`),
  **Deutsch** (`de`) and **Bahasa Indonesia** (`id`), for the dashboard page (all tabs, menus, check-ins, update
  and ledger panels, tooltips), the HUD / history / clock PNGs, reset dates (e.g. es `sáb 31 oct, 01:41`,
  de `Sa., 31. Okt., 01:41`), palette and category names and the desktop usage alerts. Any key a translation lacks
  falls back to English. Set them with `--lang es|pt|fr|de|id`, `"lang"` in the config, or the page's **Language**
  menu; regional codes such as `pt-BR`, `es-419` or `de-AT` in a config map to the base language.
- **Auto** language detection picks the new languages up from `LC_ALL` / `LC_MESSAGES` / `LANG` and
  `locale.getlocale()` (any region, e.g. `pt_BR`, `pt_PT`, `es_419`, `fr_CA`, `de_AT`, `id_ID`, the legacy
  Indonesian code `in`, and Windows names like `Spanish_Spain`), and from the Windows UI language id, including
  regional ids missing from Python's table (e.g. `0x580a` es-419) by their primary language. Unknown locales still
  fall back to English.

### Changed
- **Language** menu: **Auto** first, then the existing languages and numeric in their old order, then the new ones,
  each by its native name only; the menu is a little wider (up to 180 px) so no entry is cut off.
- Pages in the new languages use a Latin-first font stack (Segoe UI / Noto Sans / Helvetica Neue / Arial, CJK
  fonts as fallback) so `’ « »` and accented letters are not drawn full-width; the PNGs use DejaVu Sans, which
  covers every accented letter used.

### Fixed
- Clock charts: a long title no longer runs under the **Collecting data** badge; the title shrinks slightly or the
  badge moves up to the eyebrow line (English titles are drawn as before).

## [1.4.1] - 2026-10-27

### Fixed
- Title mascot washed out or turned white in browsers with an automatic dark mode (Chrome's *Auto Dark Mode for Web
  Contents*, dark-mode extensions): they lighten dark inline-SVG fills, so **Black** became a near-white blob with grey
  eyes, every colour's dark eyes and mouth went pale, and the Halloween pumpkin's carved face turned light. The page now
  declares its colour scheme (`<meta name="color-scheme">` + CSS `color-scheme`, dark or light by the theme) and the
  mascot (with a still seasonal accessory) is repainted on a `<canvas>` from its own SVG, which those modes leave
  alone; the SVG stays underneath as the fallback, blinking, the palette menu and reduced motion work as before.
- **Auto** mascot on a dark accent had white eyes and mouth (they used the button text colour). Auto now uses the
  same rule as the named colours: always dark eyes and mouth with a small white highlight.

## [1.4.0] - 2026-10-27

### Added
- Seasonal accessories on the built-in mascot (original inline SVG, fixed colours, no external assets): New Year
  firework sparkles (Dec 31 – Jan 2), a Valentine's heart (Feb 13 – 15), Children's Day carp streamers / koinobori
  (May 1 – 5), a Halloween jack-o'-lantern hat (Oct 25 – Nov 1) and a Christmas Santa hat (Dec 20 – 26), both days
  included, by the dashboard's display date (page time zone) when the page is built. The sparkles twinkle and the
  streamers sway, both still with `prefers-reduced-motion`; blinking is unchanged. New **Seasonal** menu right after
  **Mascot** (Auto (by date) / Off, default Auto; all six page languages; config key `seasonal`, browser key
  `usage.seasonal`, new `POST /api/seasonal` with token + Host/Origin checks; page-only, no chart redraw). Never on a
  custom `mascot.svg`; hidden with the mascot when it is Off, and in privacy mode.

## [1.3.0] - 2026-10-27

### Added
- Title mascot: a small original round blob (inline SVG, no external assets) right after the page title, about
  title-cap height (smaller on phones). New **Mascot** menu on the toolbar, right after **Read interval**: Auto (theme
  accent, follows the **Colours** menu; orange / teal → teal), Brown, Black, Grey, Teal, Orange, Pink, Purple, Blue,
  Green, Maroon, Off (hidden); off by default, so the page looks the same for everyone until it is picked. All six page languages. Saved like the other page menus (config key `mascot`, browser key
  `usage.mascot`, new `POST /api/mascot` with token + Host/Origin checks; page-only, no chart redraw). It blinks in
  place on click / tap, mouse hover and Enter / Space, and every 10–20 s while the tab is visible; no blinking with
  `prefers-reduced-motion`. Hidden in privacy mode, like the GitHub icon. Eyes and mouth are always dark with a small white highlight (no light eyes, rim or outline on Brown / Black).
- Custom mascot image: put your own `mascot.svg` (SVG, up to 200 KB) in the data folder (`<data_dir>`, default
  `usage-data/`, never touched by self-update) and the **Mascot** menu gets a **Custom** option (before Off; all six
  page languages) that shows it in the same spot and size. It is shown as an `<img>` with a
  `data:image/svg+xml;base64` URI, never inlined into the page, so scripts in it do not run; no blink or colours for it.
  A missing or invalid file hides the option and a saved Custom reads as Off. Hidden in privacy mode. The built-in
  mascot and the Off default are unchanged.

### Fixed
- Tests: the mid-week reset budget card test and the clock tooltip "+N more" test depended on the day / time they
  ran and failed on some days; they now pin the clock, so the full suite passes on any date.

## [1.2.4] - 2026-10-26

### Added
- Clock tab: hover (or tap on a phone, or Tab + arrow keys) a check-in dot for a tooltip with the name (`#1`, `#2` … in privacy mode), local time with UTC offset, categories, note (hidden in privacy mode) and check-in vs. session start; overlapping dots list every check-in, newest first (up to 8, then "+N more"). The PNG is unchanged: dot positions go to `clock-*.json` next to each clock PNG and are embedded in the page.

### Docs
- README: **Viewing on your phone (Tailscale)** section with Android and iOS app links, setup steps and a security note.

## [1.2.3] - 2026-10-26

### Added
- **Read interval** menu on the page toolbar, right after **Gauge animation**: 15 / 30 / 60 / 120 minutes (any other
  configured value is listed too), in all six page languages and in privacy mode. It saves `sample_interval_minutes`
  like the other page menus (`usage_config.json` when present, else `page_settings.json`) through the new
  `POST /api/interval` (token + Host/Origin checks); the sampler uses it from its next cycle. The 15–1440 clamp is
  unchanged. Disabled in manual-entry mode (`pct_source: "manual"`), where it returns `400 manual_mode`.

### Fixed
- GitHub Actions: the Linux jobs failed because the runner has no CJK font, so the charts fell back to English (as
  designed) while 8 tests expected Chinese chart text. The Linux jobs now install `fonts-noto-cjk`, and those tests are
  skipped (not failed) on a machine without any CJK font.

## [1.2.2] - 2026-10-26

### Added
- Automated tests on GitHub Actions (`.github/workflows/test.yml`): the full suite on Linux, macOS and Windows with
  Python 3.10 and 3.12 on every push and pull request to `main` (Windows is informational for now). macOS is now
  covered by these tests; the hourly automatic `/usage` read is still untested on a real Mac.

### Fixed
- Mid-week reset (e.g. a Premium -> Super upgrade resets the weekly pool to ~0% while the week window stays): the
  **Avg / Day**, **Projected (7d)** and **vs Baseline** cards, the chart's dashed forecast and `latest.json`
  (`avg_per_day`, `projected_end_pct`, `baseline`, `delta`) no longer divide the new small % by every day of the week
  (6% / 3 days = 2%/day, projected 14%, "36% under baseline"). When the cumulative % drops by more than 5 points, the
  pool is taken to restart at 0% at the midpoint between the last reading before the drop and the first one after it;
  the rate is `% now / time since the reset`, the projection `% now + rate x time left` (here ~13%/day and ~70%), the
  baseline `pace x days since the reset`, and the dashed forecast starts at the current point with that rate. The
  cards say "since mid-week reset" (zh-TW 「週中重置後」), the day's increase counts from 0% (+6%, not -78%), and
  `latest.json` gets a `midweek_reset` object (`mw` in privacy mode). Normal weeks are unchanged. When the hourly
  `samples.csv` has readings inside the gap, the drop between two of them narrows the reset-time estimate (one week-CSV
  row per day no longer pushes it hours away from the real reset).

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

[1.2.3]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#123---2026-10-26
[1.2.2]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#122---2026-10-26
[1.2.1]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#121---2026-10-25
[1.2.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#120---2026-10-25
[1.1.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#110---2026-10-25
[1.0.0]: https://github.com/yuehzhan/grokbot-usage-checkin/blob/main/CHANGELOG.md#100---2026-10-24
