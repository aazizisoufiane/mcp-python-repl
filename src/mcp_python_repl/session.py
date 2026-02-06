"""
Session management for mcp-python-repl.

Each session holds an isolated Python namespace and execution history.
Sessions are identified by an opaque ``session_id`` (UUID) and expire
after a configurable TTL.
"""

from __future__ import annotations

import threading
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

from .config import Config


@dataclass
class ExecutionRecord:
    """A single code-execution record for audit / debug."""

    timestamp: str
    code_preview: str
    status: str
    new_vars: list[str] = field(default_factory=list)
    modified_vars: list[str] = field(default_factory=list)
    error: str | None = None


@dataclass
class Session:
    """An isolated Python execution session."""

    session_id: str
    created_at: datetime
    last_used: datetime
    namespace: dict[str, Any] = field(default_factory=dict)
    history: list[ExecutionRecord] = field(default_factory=list)

    # ------------------------------------------------------------------
    def touch(self) -> None:
        self.last_used = datetime.now(timezone.utc)

    def is_expired(self, ttl_minutes: int) -> bool:
        return datetime.now(timezone.utc) - self.last_used > timedelta(minutes=ttl_minutes)

    def variable_summary(self) -> dict[str, str]:
        """Return {name: type_name} for every user variable."""
        return {
            k: type(v).__name__
            for k, v in self.namespace.items()
            if not k.startswith("_")
        }


class SessionManager:
    """Thread-safe session store with automatic eviction."""

    def __init__(self, config: Config) -> None:
        self._config = config
        self._sessions: dict[str, Session] = {}
        self._lock = threading.Lock()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def create_session(self) -> Session:
        """Create a new session, evicting stale ones first."""
        self._evict_expired()

        with self._lock:
            if len(self._sessions) >= self._config.max_sessions:
                # Evict the oldest session
                oldest_id = min(
                    self._sessions,
                    key=lambda sid: self._sessions[sid].last_used,
                )
                del self._sessions[oldest_id]

            now = datetime.now(timezone.utc)
            session = Session(
                session_id=uuid.uuid4().hex[:12],
                created_at=now,
                last_used=now,
            )
            self._sessions[session.session_id] = session
            return session

    def get_session(self, session_id: str) -> Session | None:
        """Retrieve a session by id (returns *None* if expired / missing)."""
        with self._lock:
            session = self._sessions.get(session_id)
            if session is None:
                return None
            if session.is_expired(self._config.session_ttl_minutes):
                del self._sessions[session_id]
                return None
            session.touch()
            return session

    def get_or_create(self, session_id: str | None) -> Session:
        """Return existing session or create a new one."""
        if session_id:
            session = self.get_session(session_id)
            if session is not None:
                return session
        return self.create_session()

    def delete_session(self, session_id: str) -> bool:
        with self._lock:
            return self._sessions.pop(session_id, None) is not None

    def list_sessions(self) -> list[dict[str, Any]]:
        self._evict_expired()
        with self._lock:
            return [
                {
                    "session_id": s.session_id,
                    "created_at": s.created_at.isoformat(),
                    "last_used": s.last_used.isoformat(),
                    "variable_count": len(s.variable_summary()),
                    "history_count": len(s.history),
                }
                for s in self._sessions.values()
            ]

    @property
    def count(self) -> int:
        return len(self._sessions)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _evict_expired(self) -> None:
        with self._lock:
            expired = [
                sid
                for sid, s in self._sessions.items()
                if s.is_expired(self._config.session_ttl_minutes)
            ]
            for sid in expired:
                del self._sessions[sid]
