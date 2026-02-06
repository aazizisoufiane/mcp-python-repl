"""
Configuration for mcp-python-repl.

All settings can be overridden via environment variables prefixed with REPL_.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field

# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------
DEFAULT_TIMEOUT_SECONDS = 30
DEFAULT_MAX_SESSIONS = 50
DEFAULT_SESSION_TTL_MINUTES = 120
DEFAULT_MAX_OUTPUT_BYTES = 1_048_576  # 1 MB
DEFAULT_LOG_ENTRIES = 200

# Modules / builtins that are blocked in sandboxed mode
SANDBOXED_BLOCKED_MODULES = frozenset({
    "subprocess",
    "shutil",
    "ctypes",
    "socket",
    "http.server",
    "xmlrpc",
    "ftplib",
    "smtplib",
    "telnetlib",
    "webbrowser",
    "antigravity",
})

SANDBOXED_BLOCKED_BUILTINS = frozenset({
    "exec",
    "eval",
    "compile",
    "__import__",
})


@dataclass(frozen=True)
class Config:
    """Immutable server configuration resolved from environment."""

    timeout_seconds: int = DEFAULT_TIMEOUT_SECONDS
    max_sessions: int = DEFAULT_MAX_SESSIONS
    session_ttl_minutes: int = DEFAULT_SESSION_TTL_MINUTES
    max_output_bytes: int = DEFAULT_MAX_OUTPUT_BYTES
    max_log_entries: int = DEFAULT_LOG_ENTRIES
    sandbox_enabled: bool = False
    working_directory: str = field(default_factory=os.getcwd)
    transport: str = "stdio"
    host: str = "127.0.0.1"
    port: int = 8000

    # ------------------------------------------------------------------
    @classmethod
    def from_env(cls) -> Config:
        """Build configuration from REPL_* environment variables."""

        def _env(key: str, default: str) -> str:
            return os.environ.get(f"REPL_{key}", default)

        return cls(
            timeout_seconds=int(_env("TIMEOUT", str(DEFAULT_TIMEOUT_SECONDS))),
            max_sessions=int(_env("MAX_SESSIONS", str(DEFAULT_MAX_SESSIONS))),
            session_ttl_minutes=int(_env("SESSION_TTL", str(DEFAULT_SESSION_TTL_MINUTES))),
            max_output_bytes=int(_env("MAX_OUTPUT", str(DEFAULT_MAX_OUTPUT_BYTES))),
            max_log_entries=int(_env("MAX_LOG_ENTRIES", str(DEFAULT_LOG_ENTRIES))),
            sandbox_enabled=_env("SANDBOX", "false").lower() in ("1", "true", "yes"),
            working_directory=_env("WORKDIR", os.getcwd()),
            transport=_env("TRANSPORT", "stdio"),
            host=_env("HOST", "127.0.0.1"),
            port=int(_env("PORT", "8000")),
        )
