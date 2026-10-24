"""
usage_categories.py - check-in categories shared by the dashboard tools.

Each check-in row may carry a `category` id. Ids are stable (stored in CSV);
labels are only for display. Missing, empty or unknown ids read as "other",
so check-in files written before categories existed stay readable.

Config:
  * JSON (usage_config.json):  "checkin_categories": ["web", "code", ...]
    or entries like {"id": "gaming", "label": "Gaming", "label_zh-TW": "..."}
  * INI style (comma list):    categories = web, code, break, gaming:Gaming
"""

import re
from typing import Dict, List, Optional, Sequence, Union

OTHER = "other"

# (id, English, Traditional Chinese) - order is the default menu order
DEFAULTS = [
    ("web", "Web browsing", "網頁"),
    ("code", "Code", "代碼"),
    ("break", "Break", "放風"),
    ("image", "Image generation", "畫圖"),
    ("publish", "Publishing", "發佈"),
    ("daily", "Daily check-in", "簽到"),
    ("research", "Research", "研究"),
    ("writing", "Writing", "寫作"),
    ("files", "File management", "檔案整理"),
    ("review", "Review", "檢查驗收"),
    ("maintenance", "Maintenance", "維護"),
    ("comms", "Communication", "溝通"),
    ("learning", "Learning", "學習"),
    ("other", "Other", "其他"),
]
DEFAULT_IDS = [c[0] for c in DEFAULTS]
_BUILTIN = {cid: {"en": en, "zh-TW": zh} for cid, en, zh in DEFAULTS}
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class Categories:
    """An ordered category list with labels; `other` is always present (last if added)."""

    def __init__(self, items: Optional[List[Dict[str, str]]] = None):
        self.items: List[Dict[str, str]] = []
        seen = set()
        for it in items or [dict(id=c, **_BUILTIN[c]) for c in DEFAULT_IDS]:
            cid = it["id"]
            if cid in seen:
                continue
            seen.add(cid)
            self.items.append(it)
        if OTHER not in seen:
            self.items.append(dict(id=OTHER, **_BUILTIN[OTHER]))

    @property
    def ids(self) -> List[str]:
        return [it["id"] for it in self.items]

    def normalize(self, value) -> str:
        cid = str(value or "").strip().lower()
        return cid if cid in self.ids else OTHER

    def label(self, cid: str, lang: str = "en") -> str:
        cid = self.normalize(cid)
        it = next(i for i in self.items if i["id"] == cid)
        key = "zh-TW" if str(lang).lower().startswith("zh") else "en"
        return it.get(key) or it.get("en") or cid

    def table(self, lang: str = "en") -> List[Dict[str, str]]:
        return [{"id": i["id"], "label": self.label(i["id"], lang)} for i in self.items]


def _entry(raw) -> Optional[Dict[str, str]]:
    if isinstance(raw, dict):
        cid = str(raw.get("id") or "").strip().lower()
        if not _ID_RE.match(cid):
            return None
        base = dict(_BUILTIN.get(cid, {"en": cid, "zh-TW": cid}))
        if raw.get("label"):
            base["en"] = str(raw["label"])
            base.setdefault("zh-TW", str(raw["label"]))
            if cid not in _BUILTIN:
                base["zh-TW"] = str(raw["label"])
        for k in ("label_zh-TW", "label_zh"):
            if raw.get(k):
                base["zh-TW"] = str(raw[k])
        return dict(id=cid, **base)
    text = str(raw or "").strip()
    if not text:
        return None
    cid, _, lbl = text.partition(":")
    cid = cid.strip().lower()
    if not _ID_RE.match(cid):
        return None
    base = dict(_BUILTIN.get(cid, {"en": cid, "zh-TW": cid}))
    if lbl.strip():
        base["en"] = base["zh-TW"] = lbl.strip()
    return dict(id=cid, **base)


def load(value: Union[None, str, Sequence] = None) -> Categories:
    """Build categories from a JSON list, an INI comma list, or None (defaults)."""
    if value is None or (isinstance(value, str) and not value.strip()):
        return Categories()
    raw = value.split(",") if isinstance(value, str) else list(value)
    items = [e for e in (_entry(r) for r in raw) if e]
    return Categories(items or None)
