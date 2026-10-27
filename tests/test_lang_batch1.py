"""Spanish, Portuguese, French, German and Indonesian interface languages (v1.5.0).

Every table has every English key, 'auto' maps system / Windows locales to the new codes, the page renders
in each language, accented text has glyphs in the chosen font, and the menu can switch both ways at runtime.
Everything runs in scratch dirs with made-up samples; nothing touches real data.
"""
import argparse
import json
import os
import re
import shutil
import string
import unittest
import warnings
from datetime import datetime, timezone
from pathlib import Path
from unittest import mock

os.environ.setdefault("MPLBACKEND", "Agg")

from _helpers import read_text, request, scratch, start_server, stop_server

import chart_hud  # noqa: E402
import usage_categories  # noqa: E402
import usage_checkins_ui  # noqa: E402
import usage_dates  # noqa: E402
import usage_history as uh  # noqa: E402
import usage_history_l10n as l10n  # noqa: E402
import usage_ledger  # noqa: E402
import usage_palette  # noqa: E402
from test_clock_palette import write_samples  # noqa: E402
from test_gauge_anim import write_week  # noqa: E402

NEW = ("es", "pt", "fr", "de", "id")
NATIVE = {"es": "Español", "pt": "Português", "fr": "Français", "de": "Deutsch", "id": "Bahasa Indonesia"}


def ctx(data, lang=None):
    a = argparse.Namespace(config=None, data_dir=str(data), tz="UTC", lang=lang, theme=None, pace=None,
                           palette=None, privacy=False, host=None, port=None)
    return uh.Ctx(a)


def fields(s):
    """Named placeholders in a format string ('{pct:g}' -> 'pct'); empty for non-strings."""
    if not isinstance(s, str):
        return set()
    try:
        return {f for _, f, _, _ in string.Formatter().parse(s) if f}
    except ValueError:  # a JS snippet or a literal brace, not a format string
        return set()


def strings(obj):
    if isinstance(obj, str):
        yield obj
    elif isinstance(obj, dict):
        for v in obj.values():
            yield from strings(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            yield from strings(v)


class TableTests(unittest.TestCase):
    """The raw translation tables (before the English merge) cover every English key."""

    def same_keys(self, en, tr, where):
        self.assertEqual(set(en) - set(tr), set(), f"{where}: missing keys")
        self.assertEqual(set(tr) - set(en), set(), f"{where}: keys English does not have")
        for k, v in en.items():
            if isinstance(v, dict):
                self.assertIsInstance(tr[k], dict, f"{where}.{k}")
                self.same_keys(v, tr[k], f"{where}.{k}")
            elif isinstance(v, str):
                self.assertTrue(isinstance(tr[k], str) and tr[k].strip(), f"{where}.{k} is empty")
                self.assertEqual(fields(tr[k]), fields(v), f"{where}.{k}: placeholders differ")

    def test_page_tables(self):
        pairs = (("STR", uh.STR, l10n.STR), ("PICK", uh.PICK_STR, l10n.PICK), ("MASCOT", uh.MASCOT_STR, l10n.MASCOT),
                 ("MANUAL", uh.MANUAL_STR, l10n.MANUAL), ("LEDGER", uh.LEDGER_STR, l10n.LEDGER),
                 ("UPDATE", uh.UPDATE_STR, l10n.UPDATE), ("CLOCKTIP", uh.CLOCKTIP_STR, l10n.CLOCKTIP),
                 ("CPICK", uh.CPICK_STR, l10n.CPICK), ("PAL", uh.PAL_STR, l10n.PAL))
        extra = {"STR": {"md_fmt", "months"}}  # day/month order and month names English takes from calendar
        for name, merged, raw in pairs:
            for lg in NEW:
                tr = {k: v for k, v in raw[lg].items() if k not in extra.get(name, ())}
                self.same_keys(merged["en"], tr, f"{name}[{lg}]")
                self.assertEqual(set(merged[lg]), set(merged["en"]) | set(raw[lg]), f"{name}[{lg}] merged")
        for lg in NEW:
            self.assertEqual(len(l10n.STR[lg]["months"]), 12)
            self.assertTrue(uh.GH_LABEL[lg].strip())
            self.assertEqual(set(l10n.TOOLBAR[lg]), {"pal", "pat", "off"})

    def test_mascot_options_and_back_states(self):
        for lg in NEW:
            for sub in ("opts", "sopts"):
                if sub in uh.MASCOT_STR["en"]:
                    self.assertEqual(set(uh.MASCOT_STR[lg][sub]), set(uh.MASCOT_STR["en"][sub]), (lg, sub))
            self.assertEqual(set(uh.UPDATE_STR[lg]["back_states"]), set(uh.UPDATE_STR["en"]["back_states"]), lg)

    def test_hud_tables(self):
        for lg in NEW:
            tr = chart_hud.TRANSLATIONS[lg]
            self.assertEqual(set(chart_hud.BASE_STRINGS) - set(tr), set(), lg)
            for k, v in chart_hud.BASE_STRINGS.items():
                self.assertEqual(fields(tr[k]), fields(v), (lg, k))
            self.assertIn(lg, chart_hud.FONT_CANDIDATES)

    def test_other_tables(self):
        for lg in NEW:
            ck = usage_checkins_ui.LANGS[lg]
            self.assertEqual(set(usage_checkins_ui.KEYS) - set(ck), set(), lg)
            for k in usage_checkins_ui.KEYS:
                self.assertEqual(fields(ck[k]), fields(usage_checkins_ui.EN[k]), (lg, k))
            self.assertEqual(set(usage_ledger.TEXT[lg]), set(usage_ledger.TEXT["en"]), lg)
            names = [usage_palette.LABELS[k].get(lg) for k in usage_palette.LABELS]
            self.assertNotIn(None, names, lg)
            self.assertEqual(len(set(names)), len(names), lg)  # every preset keeps a distinct name
            for t in (usage_palette.MONO_GROUP, usage_palette.CUSTOM_LABEL, usage_palette.STAR_NOTE):
                self.assertTrue(t[lg].strip(), lg)
            for cid, item in usage_categories._BUILTIN.items():
                self.assertTrue(item["label_" + lg].strip(), (lg, cid))
            self.assertEqual(len(usage_dates.WD[lg]), 7)
            self.assertEqual(len(usage_dates.MONS[lg]), 12)

    def test_missing_key_falls_back_to_english(self):
        en = {"a": "A", "b": "B", "n": {"x": "X", "y": "Y"}}
        out = uh._over_en(en, {"a": "Á", "n": {"x": "Ξ"}})
        self.assertEqual(out, {"a": "Á", "b": "B", "n": {"x": "Ξ", "y": "Y"}})
        self.assertIs(usage_checkins_ui.for_lang("xx"), usage_checkins_ui.EN)
        self.assertEqual(usage_checkins_ui.for_lang("de")["name"], usage_checkins_ui.DE["name"])
        self.assertIs(usage_checkins_ui.for_lang("numeric"), usage_checkins_ui.NUMERIC)
        with mock.patch.dict(usage_checkins_ui.LANGS, {"de": {"name": "Name"}}):
            self.assertEqual(usage_checkins_ui.for_lang("de")["note"], usage_checkins_ui.EN["note"])

    def test_no_relative_week_words(self):
        bad = re.compile(r"next week|semana que vem|próxima semana|semaine prochaine|nächste woche|minggu depan",
                         re.I)
        for lg in NEW:
            for s in strings([usage_ledger.TEXT[lg], usage_dates.FMT[lg]]):
                self.assertIsNone(bad.search(s), (lg, s))


class LocaleTests(unittest.TestCase):
    def test_locale_tags(self):
        cases = {"de_DE.UTF-8": "de", "de-AT": "de", "pt_BR.UTF-8": "pt", "pt_PT": "pt", "pt-BR": "pt",
                 "es_ES.UTF-8": "es", "es_419": "es", "es-MX": "es", "fr_FR.UTF-8": "fr", "fr_CA": "fr",
                 "fr-CH": "fr", "id_ID.UTF-8": "id", "id": "id", "in": "id", "in_ID": "id",
                 "Spanish_Spain": "es", "German_Germany": "de", "Portuguese_Brazil": "pt", "French_France": "fr",
                 "Indonesian_Indonesia": "id", "en_US.UTF-8": "en", "ru_RU.UTF-8": "en", "xx": "en",
                 "zh_TW.UTF-8": "zh-TW", "ja_JP": "ja", "ko_KR": "ko"}
        for tag, want in cases.items():
            self.assertEqual(chart_hud._normalize_locale_tag(tag), want, tag)
        for tag in (None, "", "C", "C.UTF-8", "POSIX"):
            self.assertIsNone(chart_hud._normalize_locale_tag(tag), tag)

    def test_detect_lang_env(self):
        with mock.patch.object(chart_hud.sys, "platform", "linux"):
            for env, want in (({"LANG": "de_DE.UTF-8"}, "de"), ({"LANG": "pt_BR.UTF-8"}, "pt"),
                              ({"LANG": "pt_PT.UTF-8"}, "pt"), ({"LANG": "es_419.UTF-8"}, "es"),
                              ({"LANG": "fr_CA.UTF-8"}, "fr"), ({"LANG": "id_ID.UTF-8"}, "id"),
                              ({"LANG": "in_ID"}, "id"), ({"LC_ALL": "es_ES.UTF-8", "LANG": "en_US.UTF-8"}, "es"),
                              ({"LC_MESSAGES": "fr_FR.UTF-8", "LANG": "de_DE.UTF-8"}, "fr"),
                              ({"LANG": "sv_SE.UTF-8"}, "en"), ({"LANG": "xx_YY"}, "en")):
                self.assertEqual(chart_hud.detect_lang(env), want, env)
            # nothing usable in the environment: the process locale decides
            with mock.patch.object(chart_hud.locale, "getlocale", return_value=("pt_BR", "UTF-8")):
                self.assertEqual(chart_hud.detect_lang({"LANG": "C.UTF-8"}), "pt")
            with mock.patch.object(chart_hud.locale, "getlocale", return_value=(None, None)):
                self.assertEqual(chart_hud.detect_lang({}), "en")

    def test_windows_ui_language(self):
        cases = {0x0407: "de", 0x0C07: "de", 0x0416: "pt", 0x0816: "pt", 0x0C0A: "es", 0x080A: "es",
                 0x580A: "es", 0x040C: "fr", 0x0C0C: "fr", 0x1C0C: "fr", 0x0421: "id", 0x0404: "zh-TW",
                 0x0411: "ja", 0x0409: "en", 0x0419: "en"}
        for lcid, want in cases.items():
            self.assertEqual(chart_hud.lang_from_windows_lcid(lcid), want, hex(lcid))
        self.assertIsNone(chart_hud.lang_from_windows_lcid(0x7FFF))
        fake = mock.Mock()
        fake.kernel32.GetUserDefaultUILanguage.return_value = 0x0416
        with mock.patch.object(chart_hud.sys, "platform", "win32"), \
                mock.patch.object(chart_hud.ctypes, "windll", fake, create=True):
            self.assertEqual(chart_hud.detect_lang(), "pt")

    def test_page_lang_and_dates(self):
        for v in ("pt-BR", "pt_PT", "es-419", "fr-CA", "de-AT", "id-ID"):
            self.assertEqual(uh.page_lang(v), v[:2], v)
        self.assertEqual(usage_dates.lang_key("pt-BR"), "pt")
        dt = datetime(2026, 10, 31, 1, 41, tzinfo=timezone.utc)
        for lg, s in {"es": "sáb 31 oct, 01:41", "de": "Sa., 31. Okt., 01:41"}.items():
            self.assertEqual(usage_dates.reset_date(dt, lg, timezone.utc), s)
        for lg in NEW:
            self.assertIn("01:41", usage_dates.reset_date(dt, lg, timezone.utc))


class MenuTests(unittest.TestCase):
    def test_order_and_native_names(self):
        self.assertEqual(uh.PAGE_LANGS[:7], ("auto", "zh-TW", "zh-CN", "en", "ja", "ko", "numeric"))
        self.assertEqual(uh.PAGE_LANGS[7:], ("id", "de", "es", "fr", "pt"))
        for lg, name in NATIVE.items():
            self.assertEqual(uh.LANG_NAMES[lg], name)
        self.assertEqual(set(uh.LANG_NAMES), set(uh.PAGE_LANGS) - {"auto"})
        for lg in NEW:  # native name only: one short line per item, easy to scan in a long list
            self.assertNotRegex(uh.LANG_NAMES[lg], r"[()/]")
            self.assertLessEqual(len(uh.LANG_NAMES[lg]), 24)


class PageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.dir = Path(scratch())
        cls.data = cls.dir / "data"
        cls.data.mkdir()
        write_samples(str(cls.data))

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.dir)

    def test_page_renders_in_each_language(self):
        for lg in NEW:
            c = ctx(self.data, lang=lg)
            self.assertEqual(c.lang, lg)
            uh.render_history(c)
            page = read_text(c.hist_dir / "index.html")
            self.assertIn(f'<html lang="{lg}"', page)
            self.assertIn(f"<option value='{lg}' selected>{NATIVE[lg]}</option>", page)
            for name in NATIVE.values():
                self.assertIn(f">{name}</option>", page)
            self.assertIn(uh.html.escape(l10n.TOOLBAR[lg]["pal"]), page)
            for en_only in ("Week start", "Check for updates"):
                self.assertNotIn(en_only, page, (lg, en_only))

    def test_accented_glyphs_have_a_font(self):
        from matplotlib import font_manager
        from matplotlib.ft2font import FT2Font
        text = set("éñçüãßàâêôõöäáíóú")
        for lg in NEW:
            for tbl in (chart_hud.TRANSLATIONS[lg], l10n.STR[lg], [usage_palette.LABELS[k][lg] for k in usage_palette.LABELS], usage_dates.WD[lg],
                        usage_dates.MONS[lg], usage_checkins_ui.LANGS[lg], usage_ledger.TEXT[lg]):
                text.update(ch for s in strings(tbl) for ch in s if ord(ch) > 127)
        path = font_manager.findfont(font_manager.FontProperties(family=chart_hud.FONT_CANDIDATES["de"]))
        cmap = FT2Font(path).get_charmap()
        self.assertEqual(sorted(ch for ch in text if ord(ch) not in cmap), [], path)

    def test_hud_draws_accents_without_missing_glyphs(self):
        for lg in ("de", "pt"):
            with warnings.catch_warnings(record=True) as caught:
                warnings.simplefilter("always")
                c = ctx(self.data, lang=lg)
                uh.render_history(c)
            self.assertEqual([str(w.message) for w in caught if "missing from" in str(w.message)], [], lg)
            self.assertTrue(any(c.hist_dir.rglob("*.png")))


class ClockBadgeTests(unittest.TestCase):
    def test_long_title_clears_collecting_badge(self):
        import matplotlib.pyplot as plt
        for title, lifted in (("Usage Clock — last 24 hours", False), ("Horloge d’utilisation — dernières 24 h", True)):
            fig = plt.figure(figsize=(7.2, 8.6), dpi=110)
            t = fig.text(0.05, 0.94, title, fontsize=18, fontweight="bold", va="center")
            b = fig.text(0.95, 0.94, "Collecte des données (23/24)", fontsize=11, fontweight="bold", ha="right",
                         va="center", bbox=dict(boxstyle="round,pad=0.35"))
            uh._clear_badge(fig, t, b)
            r = fig.canvas.get_renderer()
            tb, bb = t.get_window_extent(r), b.get_bbox_patch().get_window_extent(r)
            self.assertFalse(tb.overlaps(bb), title)
            self.assertEqual(b.get_position()[1] != 0.94, lifted, title)
            self.assertGreaterEqual(t.get_fontsize(), 16)
            plt.close(fig)


class SwitchTests(unittest.TestCase):
    """The language menu switches both ways while the server runs, survives reloads and a restart, and the
    HUD images are drawn again in the chosen language."""

    @classmethod
    def setUpClass(cls):
        cls.dir = scratch()
        cls.data = os.path.join(cls.dir, "data")
        os.makedirs(cls.data)
        write_samples(cls.data)
        write_week(cls.data)
        cls.env = {k: v for k, v in os.environ.items() if k not in ("LC_ALL", "LC_MESSAGES", "LANGUAGE")}
        cls.env["LANG"] = "zh_TW.UTF-8"  # 'auto' resolves to Traditional Chinese
        cls.start()

    @classmethod
    def start(cls):
        with mock.patch.dict(os.environ, cls.env, clear=True):
            cls.proc, info = start_server(["usage_history.py", "serve", "--data-dir", cls.data, "--port", "0"],
                                          os.path.join(cls.data, "history_server.json"))
        cls.url, cls.token = info["url"], read_text(info["token_file"])

    @classmethod
    def tearDownClass(cls):
        stop_server(cls.proc)
        shutil.rmtree(cls.dir)

    def post(self, lang):
        h = {"Origin": self.url.rstrip("/"), "Content-Type": "application/json", "X-Usage-Token": self.token}
        code, raw = request(self.url + "api/lang", "POST", h, json.dumps({"lang": lang}).encode())
        self.assertEqual(code, 200, lang)
        return json.loads(raw)

    def page(self):
        page = request(self.url)[1].decode("utf-8")
        sel = re.search(r'id="langsel".*?</select>', page, re.S).group(0)
        imgs = tuple(re.search(f"data-f='{f}' src='data:image/png;base64,([^']+)'", page)
                     for f in ("week-current.png", "history-1m.png"))  # the HUD and a history chart
        return (re.search(r'<html lang="([\w-]+)"', page).group(1),
                re.search(r"<option value='([\w-]+)' selected>", sel).group(1),
                None if None in imgs else tuple(hash(m.group(1)) for m in imgs))

    def saved(self):
        return json.loads(read_text(os.path.join(self.data, "page_settings.json")))["lang"]

    def test_switch_both_ways(self):
        self.assertEqual(self.page()[:2], ("zh-Hant", "auto"))
        imgs = {}
        for target, html_lang in (("en", "en"), ("auto", "zh-Hant"), ("es", "es"), ("auto", "zh-Hant"),
                                  ("de", "de"), ("zh-TW", "zh-Hant"), ("pt", "pt"), ("fr", "fr"), ("id", "id")):
            self.assertEqual(self.post(target).get("lang"), target)
            first, again = self.page(), self.page()  # a reload shows the same choice
            self.assertEqual(first, again, target)
            self.assertEqual(first[:2], (html_lang, target))
            self.assertIsNotNone(first[2], target)
            self.assertEqual(self.saved(), target)
            imgs.setdefault(html_lang, set()).add(first[2])
        for i in (0, 1):  # the HUD and the chart are drawn again for each language, and only once per language
            per_lang = {lg: {v[i] for v in vs} for lg, vs in imgs.items()}
            self.assertTrue(all(len(v) == 1 for v in per_lang.values()), per_lang.keys())
            self.assertEqual(len(set().union(*per_lang.values())), len(per_lang))
        pal = os.listdir(os.path.join(self.data, "history", "pal"))
        for lg in ("en", "zh_tw") + NEW:
            self.assertTrue(any(n.endswith("-L" + lg) for n in pal), (lg, pal))
        self.post("fr")  # the choice also survives a server restart
        stop_server(self.proc)
        type(self).start()
        self.assertEqual(self.page()[:2], ("fr", "fr"))
        self.post("auto")
        self.assertEqual(self.page()[:2], ("zh-Hant", "auto"))


if __name__ == "__main__":
    unittest.main()
