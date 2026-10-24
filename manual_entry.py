#!/usr/bin/env python3
"""Manual readings (stdlib only): the weekly % and, optionally, the next reset typed in by the user.

This is the reading path that works everywhere, including Windows machines where Grok Build is
not installed or its screen cannot be read. Used by ``usage_history.py sample --pct N --reset WHEN``
and by the page's "Enter manually" form (``POST /api/manual``).

``parse_pct`` accepts 0-200 (Grok Build shows 0-100; extra paid usage can go past 100).
``parse_reset_input`` accepts, in the display timezone:

* a weekday + time: ``Sat 09:00``, ``Saturday 9:00 AM``
* the Grok Build wording: ``Oct 31, 09:00``, ``Resets: October 31, 09:00``
* an ISO date/time: ``2026-10-31 09:00``, ``2026-10-31T09:00+02:00``

A time in the past is moved forward by whole periods, so "the reset I saw last week" still works.
A reset more than ``period_days + 1`` days ahead is refused.
"""
from datetime import datetime, timedelta

import grok_usage_reader
import usage_record

PCT_MIN, PCT_MAX = 0.0, 200.0


class ManualError(ValueError):
    pass


def parse_pct(value):
    try:
        pct = float(str(value).strip().rstrip("%").strip())
    except (TypeError, ValueError):
        raise ManualError("percentage must be a number, e.g. 42") from None
    if pct != pct or not (PCT_MIN <= pct <= PCT_MAX):  # NaN or out of range
        raise ManualError(f"percentage must be between {PCT_MIN:g} and {PCT_MAX:g}")
    return pct


def parse_reset_input(text, now, tz, period_days=7):
    """Next reset as an aware datetime, or None for empty input. Raises ManualError."""
    raw = str(text or "").strip()
    if not raw:
        return None
    if len(raw) > 64:
        raise ManualError("reset text too long")
    period = timedelta(days=int(period_days))
    now = now.astimezone(tz)
    dt = None
    if usage_record.WEEKDAY_SPEC_RE.match(raw):
        try:
            _, dt = usage_record.resolve_period_from_anchor_spec(raw, now, int(period_days), tz)
        except ValueError as exc:
            raise ManualError(str(exc)) from None
    if dt is None:
        probe = raw if raw.lower().startswith("resets") else "Resets: " + raw
        dt = grok_usage_reader.parse_reset(probe, now=now, tz=tz)
    if dt is None:
        s = raw.replace(" ", "T", 1)
        if s.endswith(("Z", "z")):
            s = s[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(s)
        except ValueError:
            raise ManualError("reset not understood; use e.g. 'Sat 09:00', 'Oct 31, 09:00' or '2026-10-31 09:00'") from None
        dt = parsed.replace(tzinfo=tz) if parsed.tzinfo is None else parsed.astimezone(tz)
    if dt <= now:
        k = int((now - dt) // period) + 1
        dt = dt + k * period
    if dt - now > period + timedelta(days=1):
        raise ManualError(f"reset is more than {int(period_days) + 1} days ahead")
    return dt
