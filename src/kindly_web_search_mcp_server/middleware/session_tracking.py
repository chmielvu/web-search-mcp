from __future__ import annotations

import time
from dataclasses import dataclass, field

from fastmcp.server.middleware import MiddlewareContext


def get_session_id(context: MiddlewareContext) -> str:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is not None:
        try:
            return fastmcp_context.session_id
        except RuntimeError:
            client_id = fastmcp_context.client_id
            if client_id:
                return client_id

    request_id = getattr(context.message, "request_id", None)
    if request_id:
        return str(request_id)

    return f"local_context:{id(fastmcp_context)}"


@dataclass
class SessionState:
    last_activity: float = field(default_factory=time.time)
    counters: dict[str, int] = field(default_factory=dict)


class SessionTracker:
    def __init__(self, timeout_seconds: float) -> None:
        self.timeout_seconds = max(1.0, timeout_seconds)
        self._sessions: dict[str, SessionState] = {}

    def _is_expired(self, state: SessionState, *, now: float | None = None) -> bool:
        current_time = now if now is not None else time.time()
        return current_time - state.last_activity > self.timeout_seconds

    def cleanup_expired_sessions(self, *, now: float | None = None) -> int:
        current_time = now if now is not None else time.time()
        expired = [
            session_id
            for session_id, state in list(self._sessions.items())
            if self._is_expired(state, now=current_time)
        ]
        for session_id in expired:
            self._sessions.pop(session_id, None)
        return len(expired)

    def get_count(self, session_id: str, key: str) -> int:
        state = self._sessions.get(session_id)
        if state is None:
            return 0
        if self._is_expired(state):
            self._sessions.pop(session_id, None)
            return 0
        return state.counters.get(key, 0)

    def increment(self, session_id: str, key: str) -> int:
        state = self._sessions.get(session_id)
        if state is None or self._is_expired(state):
            state = SessionState()
            self._sessions[session_id] = state

        state.last_activity = time.time()
        current = state.counters.get(key, 0) + 1
        state.counters[key] = current
        self.cleanup_expired_sessions()
        return current
