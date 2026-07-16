"""
Feature Cache - where Processor output lives so it can be shared
across everything that `depends_on` it, instead of each Identifier
redoing the same expensive work (decode, embed, ...) independently.

Keyed by `(processor_id, participant_id_or_None)`. `participant_id`
is None for session-scoped processors (nothing tied to one specific
participant - e.g. a shared screenshare decode). Retention is a ring
buffer capped at that processor's own declared `cache_ttl_iterations`
(see core/processor.py), and - this is the important bit -  entries
are appended only when the processor actually *runs and returns a
value*, not on every tick. That means retention is in units of "this
processor's own run count," not wall-clock time or event count: a
processor that runs rarely doesn't get its history quietly evicted
just because a lot of unrelated events flowed through the bus in the
meantime.

`satisfied()` is deliberately simple: a dependency counts as satisfied
iff the cache holds at least one entry for that key. There is no
separate "ran once a while ago and might be stale" state - under this
run-count-based retention model there's nothing that ages an entry out
except this same processor pushing a newer one in, so "has never
produced anything" is the only distinguishable "not ready" case, and
it's treated uniformly: log a warning, skip the consumer for this
tick (see SessionEngine._run_hook). No silent cold-start special case.
"""
from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from typing import Any, Optional


@dataclass(frozen=True)
class FeatureCacheEntry:
    value: Any
    t: float
    # This processor's own run index (1-based) at the moment this
    # entry was recorded - lets a temporal reader relate entries in a
    # `window()` to each other positionally, independent of how many
    # *unrelated* events happened in between.
    run_index: int


class FeatureCache:
    def __init__(self) -> None:
        self._buffers: dict[tuple[str, Optional[str]], deque[FeatureCacheEntry]] = {}
        self._run_counts: dict[tuple[str, Optional[str]], int] = defaultdict(int)

    def record(self, processor_id: str, participant_id: Optional[str], value: Any, t: float, maxlen: int) -> None:
        key = (processor_id, participant_id)
        buf = self._buffers.get(key)
        if buf is None or buf.maxlen != maxlen:
            # maxlen changing mid-session shouldn't normally happen
            # (a processor's cache_ttl_iterations is a class-level
            # constant), but rebuild-preserving-recent-entries rather
            # than silently ignoring the new cap if it ever does.
            preserved = list(buf)[-maxlen:] if buf else []
            buf = deque(preserved, maxlen=maxlen)
            self._buffers[key] = buf
        self._run_counts[key] += 1
        buf.append(FeatureCacheEntry(value=value, t=t, run_index=self._run_counts[key]))

    def latest(self, processor_id: str, participant_id: Optional[str] = None) -> Optional[FeatureCacheEntry]:
        buf = self._resolve(processor_id, participant_id)
        return buf[-1] if buf else None

    def window(self, processor_id: str, participant_id: Optional[str] = None) -> list[FeatureCacheEntry]:
        buf = self._resolve(processor_id, participant_id)
        return list(buf) if buf else []

    def satisfied(self, processor_id: str, participant_id: Optional[str] = None) -> bool:
        return bool(self._resolve(processor_id, participant_id))

    def read_only_view(self) -> "FeatureCacheReadView":
        return FeatureCacheReadView(self)

    def _resolve(self, processor_id: str, participant_id: Optional[str]) -> Optional[deque]:
        """Participant-scoped lookup first; falls back to the
        session-scoped (None) key so a participant-scoped consumer can
        depend on a session-scoped processor (e.g. a shared decode)
        without either side having to know which scope the other one
        uses."""
        if participant_id is not None:
            buf = self._buffers.get((processor_id, participant_id))
            if buf:
                return buf
        return self._buffers.get((processor_id, None))


class FeatureCacheReadView:
    """What Processors/Identifiers see via `ctx.cache` - read-only,
    same discipline-not-enforcement convention as
    ParticipantStateReadOnlyView: nothing stops a misbehaving
    processor from misusing this, the contract is by convention."""

    def __init__(self, cache: FeatureCache) -> None:
        self._cache = cache

    def latest(self, processor_id: str, participant_id: Optional[str] = None) -> Optional[FeatureCacheEntry]:
        return self._cache.latest(processor_id, participant_id)

    def window(self, processor_id: str, participant_id: Optional[str] = None) -> list[FeatureCacheEntry]:
        return self._cache.window(processor_id, participant_id)

    def satisfied(self, processor_id: str, participant_id: Optional[str] = None) -> bool:
        return self._cache.satisfied(processor_id, participant_id)
