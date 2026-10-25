#!/usr/bin/env python3
"""Average daily usage and end-of-week projection, aware of a mid-week reset (stdlib only).

Normal week: the cumulative % only goes up, so the rate is ``latest % / day index`` and the projection is
``rate x 7`` (the model the dashboard always used; unchanged).

Mid-week reset: a plan change (e.g. Premium -> Super) can reset the weekly pool to ~0% while the week window
stays the same, so the cumulative % drops between two readings. Dividing the new small % by all days of the week
then badly understates the rate (6% / 3 days = 2%/day). Instead, when a reading is more than
``MIDWEEK_RESET_MIN_DROP`` points below the one before it:

* the reset time is estimated as the **midpoint** between the last reading before the drop and the first reading
  after it (the same estimate the usage ledger's 24h-spike check uses); with hourly readings that is within
  ~30 minutes of the real reset, with daily readings it is the unbiased middle of the gap;
* the pool is taken to be ~0% at the reset, so ``rate = latest % / time since the reset`` (at least one hour);
* ``projection = latest % + rate x time left until the week ends`` (measured from the latest reading);
* the baseline to compare against is ``pace x days since the reset`` (the pace restarts with the new pool).

Only the latest drop counts (two resets in one week: the second one wins). Drops of up to
``MIDWEEK_RESET_MIN_DROP`` points are treated as corrections/noise, not resets.

Finer readings (``samples``: (datetime, % or None) pairs, e.g. the hourly samples.csv/hourly.csv) narrow the
window: when the drop shows up between two samples inside the row gap, the midpoint of those two samples is used
instead (with hourly samples the estimate is then within ~30 minutes of the real reset even when the week CSV has
only one row per day).

Each reading's time comes from its row: ``date`` (YYYY-MM-DD) + the ``HH:MM`` at the start of ``note``
(what usage_record.py writes); without a usable time the middle of that week day is used.
"""
from datetime import datetime, timedelta
import re

MIDWEEK_RESET_MIN_DROP = 5.0  # percentage points
MIN_ELAPSED_DAYS = 1.0 / 24.0  # never divide by less than one hour since the reset

_HM = re.compile(r"^\s*(\d{1,2}):(\d{2})")


def row_time(row, week_start):
    """Datetime of one CSV row, in ``week_start``'s zone. ``week_start`` is the aware/naive week start datetime."""
    try:
        day = int(row.get("day"))
    except (TypeError, ValueError):
        day = 1
    fallback = week_start + timedelta(days=day - 0.5)
    m = _HM.match(str(row.get("note") or ""))
    try:
        d = datetime.strptime(str(row.get("date") or ""), "%Y-%m-%d")
    except ValueError:
        return fallback
    if not m or int(m.group(1)) > 23 or int(m.group(2)) > 59:
        return fallback
    return d.replace(hour=int(m.group(1)), minute=int(m.group(2)), tzinfo=week_start.tzinfo)


def _refine(t0, p0, t1, p1, samples, min_drop):
    """Narrow the reset window [t0, t1] with finer readings; returns the (start, end) of the latest drop found."""
    seq = [(t0, p0)]
    for t, v in samples or ():
        if v is None:
            continue
        try:
            if t0 < t < t1:
                seq.append((t, float(v)))
        except TypeError:  # aware vs naive: these samples cannot be placed in the window
            return t0, t1
    seq.sort(key=lambda x: x[0])
    seq.append((t1, p1))
    win = (t0, t1)
    for (ta, pa), (tb, pb) in zip(seq, seq[1:]):
        if pb < pa - min_drop:
            win = (ta, tb)
    return win


def find_midweek_reset(rows, pcts, week_start, min_drop=MIDWEEK_RESET_MIN_DROP, samples=None):
    """The latest mid-week reset in ``rows`` (sorted by day; ``pcts`` = their shown %), or None.

    Returns {"index": i of the first reading after the reset, "at": estimated reset datetime,
    "before_pct": % of the reading before the drop, "after_pct": % of the first reading after it}."""
    found = None
    for i in range(1, min(len(rows), len(pcts))):
        if float(pcts[i]) < float(pcts[i - 1]) - min_drop:
            found = i
    if found is None:
        return None
    t0, t1 = row_time(rows[found - 1], week_start), row_time(rows[found], week_start)
    if samples and t1 > t0:
        t0, t1 = _refine(t0, float(pcts[found - 1]), t1, float(pcts[found]), samples, min_drop)
    at = t0 + (t1 - t0) / 2 if t1 > t0 else t1
    return {"index": found, "at": at, "before_pct": float(pcts[found - 1]), "after_pct": float(pcts[found])}


def rate_model(rows, pcts, week_start, week_end, pace, period_days=7, latest_day=None, min_drop=MIDWEEK_RESET_MIN_DROP,
               samples=None):
    """Rate / projection / baseline for the latest reading.

    Returns a dict: ``avg_day``, ``proj`` (end-of-week total), ``baseline`` (where the pace says usage should be
    now), ``reset`` (None in a normal week) and, after a reset, ``elapsed_days`` (reset -> latest reading),
    ``rem_days`` (latest reading -> week end) and ``reset_at``. ``samples``: optional finer readings that narrow
    the reset-time estimate (see the module docstring)."""
    latest_pct = float(pcts[-1]) if pcts else 0.0
    if latest_day is None:
        try:
            latest_day = int(rows[-1]["day"]) if rows else 1
        except (TypeError, ValueError, KeyError):
            latest_day = 1
    d = max(1, int(latest_day))
    out = {"avg_day": latest_pct / d, "proj": latest_pct / d * period_days, "baseline": pace * d,
           "reset": None, "elapsed_days": None, "rem_days": None, "reset_at": None}
    if week_start is None or not rows:
        return out
    reset = find_midweek_reset(rows, pcts, week_start, min_drop, samples)
    if reset is None:
        return out
    now = row_time(rows[-1], week_start)
    elapsed = max(MIN_ELAPSED_DAYS, (now - reset["at"]).total_seconds() / 86400.0)
    end = week_end if week_end is not None else week_start + timedelta(days=period_days)
    try:
        rem = max(0.0, (end - now).total_seconds() / 86400.0)
    except TypeError:  # aware vs naive: compare wall times
        rem = max(0.0, (end.replace(tzinfo=None) - now.replace(tzinfo=None)).total_seconds() / 86400.0)
    avg = latest_pct / elapsed
    out.update({"avg_day": avg, "proj": latest_pct + avg * rem, "baseline": pace * elapsed, "reset": reset,
                "elapsed_days": elapsed, "rem_days": rem, "reset_at": reset["at"]})
    return out


def baseline_at(row, pace, week_start, reset):
    """The pace baseline for one reading: ``pace x day`` normally, ``pace x days since the reset`` for readings at or
    after a mid-week reset (``reset`` from find_midweek_reset)."""
    day = int(row["day"])
    if reset is None or week_start is None:
        return pace * day
    t = row_time(row, week_start)
    if t < reset["at"]:
        return pace * day
    return pace * max(0.0, (t - reset["at"]).total_seconds() / 86400.0)
