# Changelog

All notable changes to this project are listed here, newest first. The format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and versions follow [Semantic Versioning](https://semver.org/):
MAJOR for changes that need you to do something, MINOR for new features, PATCH for fixes only.
The current version is in the `VERSION` file and shown small at the top of the page.
The changelog is kept in English only; [CHANGELOG.zh-TW.md](CHANGELOG.zh-TW.md) is no longer updated.

## [1.12.0] - 2026-10-31

### Added
- **Security test items.** `tests/test_security.py` now has 77 tests (23 before, 54 new) that start the
  real servers on a random local port in a temporary folder. They check: loopback bind and the `--allow-remote` refusal, the write token
  on every POST route, Host / Origin checks, allowed HTTP methods, path traversal on the file routes, HTML / script text in check-ins and
  built-in-tab names, malformed or oversized queries, bodies and CSV files, response headers, and the tracked files (no tokens or personal
  e-mail addresses, no `eval` / `exec` / `shell=True`). A README section lists the items. This is a list of what is tested, not a claim
  that the program has no vulnerabilities.

### Fixed
- **Six low-impact gaps the new security tests found (seven tests) are fixed**, and their tests are ordinary passing tests now:
  1. A POST with the token and `Content-Length: -1` (or any non-numeric value) no longer keeps a handler thread waiting: both servers answer
     `400` at once (`local_guard.content_length()`).
  2. `usage_view.py` refuses a declared request body above 1 MB with `413` before reading it (the history server already read at most 10 KB).
  3. A CSV field over Python's 131072-character limit (damaged or hand-edited `samples.csv` / `checkins.csv`) no longer breaks
     `/api/checkins`: the limit is raised to 16 MB, and a row the csv module still cannot parse is skipped instead of raising.
  4. `checkins.csv`: a name, note or id that starts with `=`, `+`, `-`, `@`, tab or CR is written with a leading single quote so a
     spreadsheet does not read it as a formula. The dashboard and CLI strip that one quote when they read it back, so what you typed is
     shown unchanged (a value that already starts with a quote plus such a character gets one more, so it round-trips exactly).
  5. The history server sends `X-Frame-Options: DENY` and `Content-Security-Policy: frame-ancestors 'none'`, so the page cannot be
     framed by another site.
  6. `usage_view.py` sends `X-Content-Type-Options: nosniff` and `Referrer-Policy: no-referrer`.
  7. `usage_view.py` HTML-escapes the config `title`.
- **File routes (`.png` / `.json`) in the history server** only serve one plain file name per path segment: a segment containing a
  backslash, a colon or NUL is answered with `404`. (On Windows a backslash is a path separator, so `..\x.png` could have pointed outside
  the history folder; this was not verified on Windows, the guard is a precaution and is covered by a test that runs on every platform.)

## [1.11.1] - 2026-10-31

### Changed
- **Daily tab: the increments chart follows the same order as the list.** With "Newest first" (the default) the chart now runs
  newest to oldest from the left (the latest day / hour at the left, day 1 / the oldest hour at the right); "Oldest first" (only an exact
  stored `old`) keeps the old left-to-right order. Switching the pill switches the chart too, without a reload (the reversed pictures
  already exist). Legend, baseline, colours, labels, the linked selection and the reading-gap bars are the same in both orders; the
  week overview chart is unchanged (oldest first). A peak label on a bar at the right edge of the per-hour chart is now right-aligned
  so it stays inside the card.
- **Per day and per hour keep their own order.** The pill used to flip both views at once (one shared `usage.listOrder`). Now the per-day
  view keeps `usage.listOrder` and the per-hour view has its own key `usage.listOrder.hourly`; flipping one never changes the other, and
  switching between 每日 / 每小時 never touches either choice. Each defaults to newest first. (A previously stored `old` now applies to the
  per-day view only; the per-hour view starts newest first until you flip it.)

### Docs
- **README (English and 繁體中文): Mac/Linux note for a launcher that will not start.** If `Install-Mac.command`, `install.sh` or
  `start-dashboard.sh` lacks the executable permission, the README now says to run it with `bash` or to `chmod +x` it first.

## [1.11.0] - 2026-10-31

### Added
- **Daily tab: sort the log list.** A small pill in the list header ("Newest first" / "Oldest first", translated into every page
  language) flips the order of the log list. It works in both the per-day and the per-hour views. The default is now **newest first**
  for both (today / the latest hour at the top, as the per-hour list already was; the per-day list used to start with day 1 at the
  top). The choice is remembered across reloads (localStorage key `usage.listOrder`; a stored choice always wins, an empty or
  unknown value means newest first). The today / current-row outline, the selection that is shared with the chart columns, and the
  reading-gap bars work the same in both orders. Because the list is part of the picture, the page now renders a second, reversed
  set of daily/hourly pictures (`week-daily-new.png`, `week-hourly-new.png` and their selection sprites) and shows one set or the
  other; if those files are missing the toggle is simply not shown and the list stays oldest first. The week overview list is unchanged.

### Fixed
- **Built-in tab: the disk-free bar now only ever shows the Bot computer's disk.** When the dashboard ran on your own PC with no
  disk report from your Grok Bot, it showed that PC's drive as "Bot computer disk free ... (this PC)" and could raise a false
  "Very low" warning. Now the bar (and its "?") appears only when the dashboard runs on the Bot computer itself, or when your Grok Bot's
  snapshot carries `disk_free_gb` / `disk_total_gb`; on any other machine without a report it is hidden. The local disk is never
  substituted, and the "(this PC)" label is gone. Thresholds are unchanged (orange under 23 %, red under 16 % of the disk).
  `disk_source` now only matters on the Bot computer.
- **`install.sh`, `Install-Mac.command` and `start-dashboard.sh` are marked executable** in the git history again (they were stored
  as plain files, so a `git clone` could not run `./install.sh`). Files uploaded through GitHub's web page cannot carry the
  executable bit; the README's `bash install.sh` / `bash Install-Mac.command` fallback covers that. The update notes also never show
  an "Unreleased" entry.
- **Tests / GitHub Actions:** the `tests` workflow failed on every run. The day-select picture tests assumed today was day 5 of the
  2026-10-24 week (they now pin the chart's clock), the language-switch test compared pictures that carry the clock minute (it now
  compares within one minute), and the executable-bit test is skipped on GitHub Actions (web uploads cannot set the bit) but still runs everywhere else. No dashboard behaviour changed.

## [1.10.0] - 2026-10-30

### Added
- **Built-in tab: "Bot computer disk free" bar.** A small bar at the top of the tab shows how much disk space is left on the computer
  your Grok Bot runs on, for example "Bot computer disk free: 27 GB / 128 GB". It turns orange ("Getting low") when free space is under
  23 % of the disk and red ("Very low") under 16 % (about 29.4 GB and 20.5 GB on a 128 GB disk), so it scales with the disk size.
  Labels are translated into every page language, and privacy mode keeps the numbers.
- **Help "?" next to the bar** (click, tap, Enter or Space; click outside or Esc closes it) with four tips: Grok Bot's Disk Saver
  (worded as the official docs state it), clearing caches that rebuild themselves, finding the biggest folders with `du`, and not using
  Update / Reset just to free space (Update removes installed apps and packages; Reset rebuilds from the last saved snapshot, so very
  recent changes may be lost).
- **Disk fields in the Built-in snapshot.** The copy-paste routine prompt now also asks your Grok Bot to record `disk_free_gb` and
  `disk_total_gb` (for example from `df -h ~`). The bar prefers them when the dashboard runs on another machine than the Grok Bot's
  computer; without them it reads the disk of the machine the dashboard runs on and adds "(this PC)". Old snapshots keep working.
- **New config keys:** `disk_warn_pct` (default 23) and `disk_danger_pct` (default 16); `disk_source` (`auto` / `local` / `snapshot`).
  The fixed `disk_warn_gb` / `disk_danger_gb`, if you set them, still win over the percentages.

## [1.9.0] - 2026-10-30

### Added
- **One-click install for Windows, Mac and Linux** (double-click `Install-Windows.cmd`, `Install-Mac.command` or `install.sh`).
  It asks nothing: it installs what is missing (Python via winget on Windows, Grok Build with xAI's official installer, `tmux`,
  the Python packages; a private `.venv` when the system Python refuses `pip`), sets reading to `auto`, starts the dashboard and
  prints its address. Safe to run again. Options: `--dry-run`, `--manual`, `--no-grok-auto`, `--no-start`, `--no-shortcut`,
  and `--ask` if you want a "Continue? [y/N]" first (Windows: `-DryRun`, `-Manual`, `-NoGrokAuto`, `-NoStart`, `-NoShortcut`, `-Ask`).
- **AUTO by default**: the installer sets `pct_source` to `auto` and makes `auto` Grok Build's saved default permission mode
  (`[ui] permission_mode = "auto"` in `~/.grok/config.toml`, the only line it changes; sign-in data is never opened). Running it
  again puts both back to auto. `--no-grok-auto` leaves Grok Build's mode alone; always-approve is not lowered.
- On Windows the installer switches off console QuickEdit for its own window only, so a stray click cannot freeze the install
  (nothing is written to the registry).
- A **Grok Usage Dashboard** shortcut on the Desktop (Windows `.lnk` running `start.ps1`, Mac `.command`, Linux `.desktop` when
  `~/Desktop` exists; new `start-dashboard.sh`). Made again on every run; a failure is only a warning.
- README: an **Uninstall** section (stop the dashboard, delete the shortcut, delete the folder; Grok Build is not removed).
- **Mac support is beta**: the Mac installer has not been tested on a real Mac yet; please report how it goes via a GitHub issue.

### Changed
- README: the one-click install and Uninstall sections come first and stay visible; the long sections are collapsed behind
  `<details>` blocks (the old Linux "Quick install" is now "Manual install").
- The installer's closing sign-in reminder appears only when no Grok Build sign-in file is found (it only checks that the file
  exists and never opens it).

### Fixed
- The background sampler no longer gives up after one failed read. A first read that was "unavailable" (seen once on Windows
  right after installing Grok Build: `maximum recursion depth exceeded`) is now tried again after 20 s and 60 s before the sampler
  waits for the next interval; each retry is logged, and only the last failed try is recorded as `unavailable`. A sign-in request
  or a missing Grok Build / tmux / pywinpty is not retried. A `RecursionError` from the reader is now an ordinary
  `unavailable` read with a clearer message.

## [1.8.0] - 2026-10-29

### Added
- **Built-in** tab, right after **Check-ins** (zh-TW: 「內建」): shows Grok Bot's built-in usage report, which the dashboard
  cannot read by itself: plan, weekly limit used, Grok Bot's share, a note when the limit is shared with other apps, the reset
  time with a countdown, share bars **by bot**, and the **by activity** split with the routines nested under *Routines*
  (name, share, about N runs a week). Shows "as of ..." with the snapshot age (older than 36 h is flagged in the notice colour)
  and, when the previous snapshot is from the same period and reset, the change in percentage points. Theme-aware (all
  palettes, light and dark) and translated in every page language.
- Snapshots are plain JSON files in `usage-data/breakdown/` (a documented, leniently parsed format: missing fields, `"<1%"`
  and `"61.1% used"` text, maps instead of lists are all accepted). Your Grok Bot writes them from a routine; the README has a
  ready-to-paste prompt in English and Traditional Chinese. Every snapshot is kept (never overwritten) for later trend views;
  only the newest is shown. The empty state explains the setup.
- `python3 usage_history.py breakdown [FILE|-]` imports a snapshot by hand (a ```` ```json ```` fence or surrounding text is fine) or
  prints the newest one. The served page rebuilds itself when a newer snapshot file appears.
- Privacy mode: bot and routine names show as `#1`, `#2` ..., and the plan, extra-usage source, notes and prompt are hidden.

## [1.7.1] - 2026-10-29

### Fixed
- **Daily** tab, per-hour view: the hourly list (*Hourly Log*) is no longer limited to the newest day. It lists every hour row
  of the 7-day chart (newest first) in a scrollable list with an always-visible, draggable scrollbar on its right edge. The
  selection still works for any hour in both directions: clicking a bar selects and scrolls to its row, clicking a row moves the
  light mask on the chart, and the mask stays at least a few pixels wide (also for the first and last hour of the week). The newest
  hour is still selected when the page opens, and reading-gap rows / bands are unchanged. (Before, the list was cut after 14 rows.)

## [1.7.0] - 2026-10-28

### Added
- **Daily** tab, per-day and per-hour views: the chart columns and the log cards / rows now share **one selection**.
  It starts on today (per hour: the latest hour). Clicking either side moves a light mask on the chart column and the
  highlight on the list. Days that are not recorded yet can be selected too. The selection is not remembered across a
  reload.

### Changed
- Per-hour view: reading-gap bars are now a low neutral grey band drawn at their average per hour. The purple dotted
  box and the inflated y-scale are gone; the label text is unchanged.

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
