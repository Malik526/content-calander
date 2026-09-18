"""
slot_matcher.py — Deterministic content_slot selection.

What it does:
  Given a classified pillar, finds the earliest OPEN content_slot for that
  pillar scheduled after now. This is plain deterministic code — the AI
  classifier decides the pillar, this module decides the date, and the two
  responsibilities never mix (see docs/decisions/0001-video-ingestion-pipeline.md).

Dependencies:
  content_store.py, config.TIMEZONE
"""

from datetime import datetime
from zoneinfo import ZoneInfo

from content_automation.config import TIMEZONE
from content_automation.persistence.content_store import ContentStore, SlotRecord


def now_in_config_timezone() -> datetime:
    """Current wall-clock time in config.TIMEZONE, tzinfo stripped.

    content_slots.scheduled_at is stored as a naive local datetime isoformat
    (matching generate_calendar.build_event_body), so "now" must be computed
    in the same timezone before the naive-string comparison in the query.
    """
    return datetime.now(ZoneInfo(TIMEZONE)).replace(tzinfo=None)


def select_slot(store: ContentStore, pillar_key: str, now: datetime | None = None) -> SlotRecord | None:
    """Return the earliest OPEN content_slot for pillar_key scheduled after now, if any."""
    after = now or now_in_config_timezone()
    return store.find_earliest_open_slot(pillar_key, after.isoformat())


def select_slot_fifo(store: ContentStore, now: datetime | None = None) -> SlotRecord | None:
    """Return the earliest OPEN content_slot scheduled at/after now, ignoring
    pillar entirely — the FIFO routing mode matcher. No AI/classifier
    involvement, same as select_slot."""
    after = now or now_in_config_timezone()
    return store.find_earliest_open_slot_fifo(after.isoformat())
