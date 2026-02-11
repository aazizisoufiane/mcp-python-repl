"""
Python code executor with timeout protection and optional sandboxing.
"""

from __future__ import annotations

import builtins
import io
import signal
import traceback
from contextlib import redirect_stderr, redirect_stdout
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from .config import SANDBOXED_BLOCKED_BUILTINS, SANDBOXED_BLOCKED_MODULES, Config
from .session import ExecutionRecord, Session


@dataclass
class ExecutionResult:
    """Structured result of a single code execution."""

    status: str  # "completed" | "error" | "timeout"
    result: Any | None = None
    stdout: str = ""
    stderr: str = ""
    error_type: str | None = None
    error_message: str | None = None
    error_traceback: str | None = None
    error_line: int | None = None
    hint: str | None = None
    new_vars: list[str] | None = None
    modified_vars: list[str] | None = None


class TimeoutError(Exception):  # noqa: A001
    """Raised when code execution exceeds the allowed time."""


def _timeout_handler(signum: int, frame: Any) -> None:
    raise TimeoutError("Code execution timed out")


def _make_sandbox_builtins(original_builtins: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of builtins with dangerous items removed."""
    safe = dict(vars(builtins))

    # Remove dangerous builtins
    for name in SANDBOXED_BLOCKED_BUILTINS:
        safe.pop(name, None)

    # Replace __import__ with a restricted version
    _real_import = builtins.__import__

    def _restricted_import(
        name: str,
        globals: dict | None = None,
        locals: dict | None = None,
        fromlist: tuple = (),
        level: int = 0,
    ) -> Any:
        top_level = name.split(".")[0]
        if top_level in SANDBOXED_BLOCKED_MODULES:
            raise ImportError(
                f"Module '{name}' is blocked in sandbox mode. "
                f"Blocked modules: {', '.join(sorted(SANDBOXED_BLOCKED_MODULES))}"
            )
        return _real_import(name, globals, locals, fromlist, level)

    safe["__import__"] = _restricted_import
    return safe


def execute_code(
    code: str,
    session: Session,
    config: Config,
) -> ExecutionResult:
    """
    Execute *code* inside *session*'s namespace.

    - Captures stdout / stderr
    - Enforces timeout via SIGALRM (Unix) or threading (Windows)
    - Optionally sandboxes dangerous builtins / imports
    - Persists new / modified variables in session namespace
    - Records execution in session history
    """
    if not code or not code.strip():
        return ExecutionResult(
            status="error", error_type="ValueError", error_message="No code provided"
        )

    # Build execution namespace
    exec_ns: dict[str, Any] = {**session.namespace}

    if config.sandbox_enabled:
        exec_ns["__builtins__"] = _make_sandbox_builtins(vars(builtins))
    else:
        exec_ns["__builtins__"] = builtins

    exec_ns.pop("result", None)  # Clear previous result

    stdout_buf = io.StringIO()
    stderr_buf = io.StringIO()

    # ------------------------------------------------------------------
    # Execute with timeout
    # ------------------------------------------------------------------
    use_signal = hasattr(signal, "SIGALRM")

    try:
        if use_signal:
            old_handler = signal.signal(signal.SIGALRM, _timeout_handler)
            signal.alarm(config.timeout_seconds)

        try:
            with redirect_stdout(stdout_buf), redirect_stderr(stderr_buf):
                exec(code, exec_ns)  # noqa: S102
        finally:
            if use_signal:
                signal.alarm(0)
                signal.signal(signal.SIGALRM, old_handler)

    except TimeoutError:
        _record(session, code, "timeout")
        return ExecutionResult(
            status="timeout",
            error_type="TimeoutError",
            error_message=f"Execution exceeded {config.timeout_seconds}s limit",
            hint="Break your code into smaller chunks or increase REPL_TIMEOUT.",
            stdout=_truncate(stdout_buf.getvalue(), config.max_output_bytes),
            stderr=_truncate(stderr_buf.getvalue(), config.max_output_bytes),
        )

    except SyntaxError as exc:
        _record(session, code, "error", error=str(exc))
        return ExecutionResult(
            status="error",
            error_type="SyntaxError",
            error_message=exc.msg,
            error_line=exc.lineno,
            hint="Check your Python syntax.",
        )

    except NameError as exc:
        _record(session, code, "error", error=str(exc))
        available = list(session.variable_summary().keys())
        return ExecutionResult(
            status="error",
            error_type="NameError",
            error_message=str(exc),
            hint=(
                "Variable not found. Variables persist by their ASSIGNED NAME, "
                "not through 'result'. Use repl_list_namespace() to see available variables. "
                f"Currently available: {available}"
            ),
        )

    except Exception as exc:
        _record(session, code, "error", error=str(exc))
        return ExecutionResult(
            status="error",
            error_type=type(exc).__name__,
            error_message=str(exc),
            error_traceback=traceback.format_exc(),
            stdout=_truncate(stdout_buf.getvalue(), config.max_output_bytes),
            stderr=_truncate(stderr_buf.getvalue(), config.max_output_bytes),
        )

    # ------------------------------------------------------------------
    # Persist namespace changes
    # ------------------------------------------------------------------
    new_vars: list[str] = []
    modified_vars: list[str] = []

    for key, value in exec_ns.items():
        if key.startswith("_") or key in ("__builtins__", "result"):
            continue
        if key not in session.namespace:
            new_vars.append(key)
            session.namespace[key] = value
        elif session.namespace.get(key) is not value:
            modified_vars.append(key)
            session.namespace[key] = value

    stdout = _truncate(stdout_buf.getvalue(), config.max_output_bytes)
    stderr = _truncate(stderr_buf.getvalue(), config.max_output_bytes)
    result_val = exec_ns.get("result")

    _record(session, code, "completed", new_vars=new_vars, modified_vars=modified_vars)

    return ExecutionResult(
        status="completed",
        result=result_val,
        stdout=stdout,
        stderr=stderr,
        new_vars=new_vars or None,
        modified_vars=modified_vars or None,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _truncate(text: str, max_bytes: int) -> str:
    if len(text) <= max_bytes:
        return text
    return text[:max_bytes] + f"\n... [truncated, {len(text)} total chars]"


def _record(
    session: Session,
    code: str,
    status: str,
    *,
    new_vars: list[str] | None = None,
    modified_vars: list[str] | None = None,
    error: str | None = None,
) -> None:
    preview = code[:120].replace("\n", "\\n")
    if len(code) > 120:
        preview += "..."
    session.history.append(
        ExecutionRecord(
            timestamp=datetime.now(timezone.utc).isoformat(),
            code_preview=preview,
            status=status,
            new_vars=new_vars or [],
            modified_vars=modified_vars or [],
            error=error,
        )
    )
