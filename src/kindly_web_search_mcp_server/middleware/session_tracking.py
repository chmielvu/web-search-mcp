from __future__ import annotations

import logging
import time
import uuid
from dataclasses import dataclass, field

from fastmcp.server.middleware import MiddlewareContext

from ..utils.observability import emit_observability_event

logger = logging.getLogger(__name__)

# Stable per-process fallback session id. Using a UUID generated once at import
# time (rather than id(), whose memory addresses are reused and unstable) avoids
# cross-request state pollution in the expensive-tool-protection middleware.
_FALLBACK_SESSION_ID = f"local_context:{uuid.uuid4().hex}"


def get_session_id(context: MiddlewareContext) -> str:
    fastmcp_context = context.fastmcp_context
    if fastmcp_context is not None:
        try:
            session_id = fastmcp_context.session_id
            if session_id:
                return str(session_id)
        except Exception:
            pass
        try:
            client_id = fastmcp_context.client_id
            if client_id:
                return str(client_id)
        except Exception:
            pass

    request_id = getattr(context.message, "request_id", None)
    if request_id:
        return str(request_id)

    return _FALLBACK_SESSION_ID

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
            emit_observability_event(
                logger,
                "session.expired",
                session_id=session_id,
                session_timeout_seconds=self.timeout_seconds,
            )
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
        is_new_session = state is None or self._is_expired(state)
        if is_new_session:
            state = SessionState()
            self._sessions[session_id] = state

        assert state is not None  # guaranteed by the branch above
        current = state.counters.get(key, 0) + 1
        state.counters[key] = current
        emit_observability_event(
            logger,
            "session.started" if is_new_session else "session.activity",
            session_id=session_id,
            tool_name=key,
            tool_count=current,
            session_timeout_seconds=self.timeout_seconds,
        )
        state.last_activity = time.time()
        self.cleanup_expired_sessions()
        return current
