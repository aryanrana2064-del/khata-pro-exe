"""The announcement queue.

Two rules drive the design. Sentences never overlap - the manager pops one item, speaks it, and only then asks
for the next. And a warning must not sit behind a queue of routine confirmations, so a higher priority item is
taken first once the sentence in progress has finished; speech already started is never cut off.
"""
from __future__ import annotations

import heapq
import itertools
import time
from dataclasses import dataclass, field

from .voice_event_dispatcher import PRIORITY_NORMAL

DUPLICATE_WINDOW_SECONDS = 4.0
MAX_PENDING = 32


@dataclass(order=True)
class _Entry:
    sort_key: tuple
    text: str = field(compare=False)
    event: str = field(compare=False, default="")
    priority: int = field(compare=False, default=PRIORITY_NORMAL)
    key: str = field(compare=False, default="")


class VoiceQueue:
    """Priority queue that keeps insertion order inside one priority level."""

    def __init__(self, clock=time.monotonic, duplicate_window: float = DUPLICATE_WINDOW_SECONDS,
                 max_pending: int = MAX_PENDING):
        self._heap: list[_Entry] = []
        self._sequence = itertools.count()
        self._clock = clock
        self._duplicate_window = duplicate_window
        self._max_pending = max_pending
        self._seen: dict[str, float] = {}

    def push(self, text: str, event: str = "", priority: int = PRIORITY_NORMAL, key: str = "") -> bool:
        """Queue a sentence. Returns False when it was dropped as a duplicate or as overflow."""
        if not text.strip():
            return False
        now = self._clock()
        if key:
            last = self._seen.get(key)
            if last is not None and now - last < self._duplicate_window:
                return False
            self._seen[key] = now
        if len(self._heap) >= self._max_pending:
            # Rapid-fire events must not grow without bound; the oldest routine item is the least useful one.
            drop = next((i for i, entry in enumerate(sorted(self._heap, key=lambda e: e.sort_key))
                         if entry.priority <= PRIORITY_NORMAL), None)
            if drop is None:
                return False
            self._heap = sorted(self._heap, key=lambda e: e.sort_key)
            self._heap.pop(drop)
        heapq.heappush(self._heap, _Entry((-priority, next(self._sequence)), text, event, priority, key))
        return True

    def pop(self) -> _Entry | None:
        return heapq.heappop(self._heap) if self._heap else None

    def peek(self) -> str:
        return self._heap[0].text if self._heap else ""

    def clear(self) -> int:
        """Forget everything waiting. Whatever is already being spoken is left alone."""
        count = len(self._heap)
        self._heap.clear()
        self._seen.clear()
        return count

    def __len__(self) -> int:
        return len(self._heap)

    def __bool__(self) -> bool:
        return bool(self._heap)
