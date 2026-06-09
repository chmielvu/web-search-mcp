"""TTL session state for search-side labels and suppression signals."""

from __future__ import annotations

import time
from dataclasses import dataclass, field


@dataclass
class SessionState:
    last_activity: float = field(default_factory=time.time)
    counters: dict[str, int] = field(default_factory=dict)
    seen_urls: set[str] = field(default_factory=set)
    last_intent: str | None = None


class SessionStateStore:
    def __init__(self, ttl_seconds: float = 900.0) -> None:
        self.ttl_seconds = max(60.0, ttl_seconds)
        self._sessions: dict[str, SessionState] = {}

    def get(self, session_id: str) -> SessionState:
        state = self._sessions.get(session_id)
        if state is None or time.time() - state.last_activity > self.ttl_seconds:
            state = SessionState()
            self._sessions[session_id] = state
        state.last_activity = time.time()
        return state

    def mark_seen(self, session_id: str, url: str) -> None:
        self.get(session_id).seen_urls.add(url)

    def increment(self, session_id: str, key: str) -> int:
        state = self.get(session_id)
        current = state.counters.get(key, 0) + 1
        state.counters[key] = current
        return current


_SESSION_STATE = SessionStateStore()


def get_session_state_store() -> SessionStateStore:
    return _SESSION_STATE
