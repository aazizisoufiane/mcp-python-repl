"""
mcp-python-repl — A production-grade MCP server providing a persistent Python REPL.

Features:
  - Multi-session with automatic TTL eviction
  - Persistent namespace across calls (variables survive between executions)
  - Execution timeout protection (SIGALRM on Unix)
  - Optional sandboxing (block dangerous modules / builtins)
  - Package installation via pip/uv
  - Dual transport: stdio (default) and streamable-http
  - Configurable via REPL_* environment variables

Run:
  # stdio (default)
  mcp-python-repl

  # streamable HTTP
  REPL_TRANSPORT=streamable-http REPL_PORT=8000 mcp-python-repl
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from dataclasses import asdict
from typing import Optional

from mcp.server.fastmcp import FastMCP
from pydantic import BaseModel, Field

from .config import Config
from .executor import ExecutionResult, execute_code
from .session import SessionManager

# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

_config = Config.from_env()
_sessions = SessionManager(_config)

mcp = FastMCP(
    "python_repl_mcp",
    instructions=(
        "Persistent Python REPL server. Variables assigned in repl_run_code() "
        "are automatically stored in the session and available in later calls. "
        "Access them DIRECTLY by name — the 'result' variable is only for "
        "returning output to the caller and does NOT persist."
    ),
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _var_preview(value: object, max_len: int = 100) -> str:
    """Human-readable preview of a Python object."""
    type_name = type(value).__name__
    if isinstance(value, dict):
        keys = list(value.keys())[:5]
        more = f" +{len(value) - 5} more" if len(value) > 5 else ""
        return f"dict({len(value)} keys): {keys}{more}"
    if isinstance(value, (list, tuple)):
        return f"{type_name}({len(value)} items)"
    if isinstance(value, str):
        preview = value[:60].replace("\n", "\\n")
        return f'str({len(value)}): "{preview}{"…" if len(value) > 60 else ""}"'
    if isinstance(value, (int, float, bool)):
        return f"{type_name}: {value}"
    preview = str(value)[:max_len]
    return f"{type_name}: {preview}{'…' if len(str(value)) > max_len else ''}"


def _result_to_json(res: ExecutionResult, session_id: str, namespace_keys: list[str]) -> str:
    """Serialize an ExecutionResult to a clean JSON response."""
    out: dict = {"status": res.status, "session_id": session_id}

    if res.result is not None:
        try:
            json.dumps(res.result)  # test serializability
            out["result"] = res.result
        except (TypeError, ValueError):
            out["result"] = str(res.result)

    if res.stdout:
        out["stdout"] = res.stdout
    if res.stderr:
        out["stderr"] = res.stderr

    if res.new_vars:
        out["new_variables"] = res.new_vars
    if res.modified_vars:
        out["modified_variables"] = res.modified_vars

    if res.error_type:
        out["error_type"] = res.error_type
        out["error_message"] = res.error_message
    if res.error_traceback:
        out["traceback"] = res.error_traceback
    if res.error_line is not None:
        out["line"] = res.error_line
    if res.hint:
        out["hint"] = res.hint

    out["namespace"] = {"total": len(namespace_keys), "variables": namespace_keys}
    return json.dumps(out, indent=2, default=str, ensure_ascii=False)


# ===================================================================
# Pydantic input models
# ===================================================================

class RunCodeInput(BaseModel):
    """Input for executing Python code."""

    code: str = Field(
        ...,
        description=(
            "Python code to execute. Assign variables to persist them across calls. "
            "Use 'result' to return output to the caller (result does NOT persist)."
        ),
        min_length=1,
        max_length=500_000,
    )
    session_id: Optional[str] = Field(
        default=None,
        description="Session ID to resume. Omit to create a new session.",
        max_length=20,
    )


class RunFileInput(BaseModel):
    """Input for executing a Python file."""

    file_path: str = Field(
        ...,
        description="Absolute or relative path to the .py file.",
        min_length=1,
    )
    session_id: Optional[str] = Field(default=None, description="Session ID to resume.", max_length=20)
    args: Optional[str] = Field(default=None, description="Space-separated CLI arguments.")


class InstallPackageInput(BaseModel):
    """Input for installing a Python package."""

    package: str = Field(
        ...,
        description="Package specifier (e.g. 'pandas', 'requests>=2.31').",
        min_length=1,
        max_length=200,
    )


class SessionIdInput(BaseModel):
    """Input requiring a session ID."""

    session_id: str = Field(..., description="Target session ID.", max_length=20)


class GetVarInput(BaseModel):
    """Input for retrieving a variable."""

    session_id: str = Field(..., description="Session ID.", max_length=20)
    var_name: str = Field(..., description="Variable name.", min_length=1, max_length=200)


class SetVarInput(BaseModel):
    """Input for setting a variable."""

    session_id: str = Field(..., description="Session ID.", max_length=20)
    var_name: str = Field(..., description="Variable name.", min_length=1, max_length=200)
    json_value: str = Field(..., description="JSON string to parse and store.")


class HistoryInput(BaseModel):
    """Input for retrieving execution history."""

    session_id: str = Field(..., description="Session ID.", max_length=20)
    last_n: int = Field(default=10, description="Number of entries to return.", ge=1, le=100)


# ===================================================================
# Tools
# ===================================================================

@mcp.tool(
    name="repl_run_code",
    annotations={
        "title": "Execute Python Code",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def repl_run_code(params: RunCodeInput) -> str:
    """Execute Python code with a PERSISTENT namespace.

    Variables you assign are STORED and available in subsequent calls.
    Access them DIRECTLY by name (e.g. ``my_data``, ``df``).

    The ``result`` variable is ONLY for returning output to the caller.
    It does NOT persist between calls — use named variables instead.

    Correct workflow::

        Call 1: data = load_csv("input.csv"); result = f"loaded {len(data)} rows"
        Call 2: filtered = [r for r in data if r["active"]]; result = len(filtered)

    Returns:
        JSON with execution result, new/modified variables, and namespace summary.
    """
    session = _sessions.get_or_create(params.session_id)
    res = execute_code(params.code, session, _config)
    ns_keys = list(session.variable_summary().keys())
    return _result_to_json(res, session.session_id, ns_keys)


@mcp.tool(
    name="repl_run_file",
    annotations={
        "title": "Execute Python File",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": False,
        "openWorldHint": True,
    },
)
def repl_run_file(params: RunFileInput) -> str:
    """Execute a Python file inside the persistent session.

    Variables defined in the file become available for later use.

    Args:
        params: File path, optional session ID, and optional CLI args.

    Returns:
        JSON with execution result, file metadata, and namespace summary.
    """
    file_path = os.path.expanduser(params.file_path)
    if not os.path.isabs(file_path):
        file_path = os.path.abspath(file_path)

    if not os.path.isfile(file_path):
        return json.dumps({
            "status": "error",
            "error_type": "FileNotFoundError",
            "message": f"File not found: {file_path}",
        }, indent=2)

    try:
        with open(file_path, encoding="utf-8") as fh:
            code = fh.read()
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "error_type": type(exc).__name__,
            "message": str(exc),
        }, indent=2)

    session = _sessions.get_or_create(params.session_id)

    # Inject __file__ and sys.argv
    session.namespace["__file__"] = file_path
    original_argv = sys.argv[:]
    sys.argv = [file_path] + (params.args.split() if params.args else [])

    try:
        res = execute_code(code, session, _config)
        ns_keys = list(session.variable_summary().keys())
        out = json.loads(_result_to_json(res, session.session_id, ns_keys))
        out["executed_file"] = file_path
        out["file_size_bytes"] = len(code)
        return json.dumps(out, indent=2, default=str, ensure_ascii=False)
    finally:
        sys.argv = original_argv


@mcp.tool(
    name="repl_install_package",
    annotations={
        "title": "Install Python Package",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": True,
    },
)
def repl_install_package(params: InstallPackageInput) -> str:
    """Install a Python package using pip (or uv if available).

    The package becomes importable in all sessions immediately.

    Args:
        params: Package specifier (e.g. ``pandas``, ``requests>=2.31``).

    Returns:
        JSON with installation status and output.
    """
    # Prefer uv for speed, fall back to pip
    uv_available = subprocess.run(
        ["uv", "--version"], capture_output=True, timeout=5  # noqa: S603, S607
    ).returncode == 0 if _is_command_available("uv") else False

    if uv_available:
        cmd = ["uv", "pip", "install", params.package]
    else:
        cmd = [sys.executable, "-m", "pip", "install", "--break-system-packages", params.package]

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_config.timeout_seconds * 4,  # give installs more time
        )
        return json.dumps({
            "status": "ok" if proc.returncode == 0 else "error",
            "package": params.package,
            "installer": "uv" if uv_available else "pip",
            "returncode": proc.returncode,
            "stdout": proc.stdout[-2000:] if proc.stdout else "",
            "stderr": proc.stderr[-2000:] if proc.stderr else "",
        }, indent=2)
    except subprocess.TimeoutExpired:
        return json.dumps({
            "status": "timeout",
            "package": params.package,
            "message": "Installation timed out.",
        }, indent=2)
    except Exception as exc:
        return json.dumps({
            "status": "error",
            "package": params.package,
            "error": str(exc),
        }, indent=2)


# -------------------------------------------------------------------
# Namespace management
# -------------------------------------------------------------------

@mcp.tool(
    name="repl_list_namespace",
    annotations={
        "title": "List Session Variables",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_list_namespace(params: SessionIdInput) -> str:
    """List all variables stored in a session's namespace.

    Returns:
        JSON with variable names, types, and preview values.
    """
    session = _sessions.get_session(params.session_id)
    if session is None:
        return json.dumps({"status": "error", "message": f"Session '{params.session_id}' not found."})

    variables = {
        k: {"type": type(v).__name__, "preview": _var_preview(v)}
        for k, v in session.namespace.items()
        if not k.startswith("_")
    }
    return json.dumps({
        "status": "ok",
        "session_id": params.session_id,
        "total": len(variables),
        "variables": variables,
    }, indent=2, ensure_ascii=False)


@mcp.tool(
    name="repl_get_variable",
    annotations={
        "title": "Get Variable Value",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_get_variable(params: GetVarInput) -> str:
    """Retrieve the full value of a variable from a session.

    Returns:
        JSON with the variable name, type, and serialized value.
    """
    session = _sessions.get_session(params.session_id)
    if session is None:
        return json.dumps({"status": "error", "message": f"Session '{params.session_id}' not found."})

    if params.var_name not in session.namespace:
        return json.dumps({
            "status": "error",
            "message": f"Variable '{params.var_name}' not found.",
            "available": list(session.variable_summary().keys()),
        }, indent=2)

    value = session.namespace[params.var_name]
    try:
        serialized = json.loads(json.dumps(value, default=str, ensure_ascii=False))
    except (TypeError, ValueError):
        serialized = str(value)

    return json.dumps({
        "status": "ok",
        "variable": params.var_name,
        "type": type(value).__name__,
        "value": serialized,
    }, indent=2, ensure_ascii=False)


@mcp.tool(
    name="repl_set_variable",
    annotations={
        "title": "Set Variable",
        "readOnlyHint": False,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_set_variable(params: SetVarInput) -> str:
    """Set a variable in a session from a JSON string.

    Useful for injecting data from external sources.

    Returns:
        Confirmation with variable type and preview.
    """
    session = _sessions.get_session(params.session_id)
    if session is None:
        return json.dumps({"status": "error", "message": f"Session '{params.session_id}' not found."})

    try:
        value = json.loads(params.json_value)
    except json.JSONDecodeError:
        value = params.json_value  # store as raw string

    session.namespace[params.var_name] = value
    return json.dumps({
        "status": "ok",
        "variable": params.var_name,
        "type": type(value).__name__,
        "preview": _var_preview(value),
    }, indent=2)


@mcp.tool(
    name="repl_delete_variable",
    annotations={
        "title": "Delete Variable",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_delete_variable(params: GetVarInput) -> str:
    """Delete a specific variable from a session's namespace.

    Returns:
        Confirmation with remaining variables.
    """
    session = _sessions.get_session(params.session_id)
    if session is None:
        return json.dumps({"status": "error", "message": f"Session '{params.session_id}' not found."})

    if params.var_name not in session.namespace:
        return json.dumps({
            "status": "error",
            "message": f"Variable '{params.var_name}' not found.",
            "available": list(session.variable_summary().keys()),
        }, indent=2)

    del session.namespace[params.var_name]
    return json.dumps({
        "status": "ok",
        "deleted": params.var_name,
        "remaining": list(session.variable_summary().keys()),
    }, indent=2)


@mcp.tool(
    name="repl_clear_namespace",
    annotations={
        "title": "Clear Session Namespace",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_clear_namespace(params: SessionIdInput) -> str:
    """Clear ALL variables from a session. Cannot be undone.

    Returns:
        Confirmation with list of cleared variables.
    """
    session = _sessions.get_session(params.session_id)
    if session is None:
        return json.dumps({"status": "error", "message": f"Session '{params.session_id}' not found."})

    cleared = list(session.variable_summary().keys())
    session.namespace.clear()
    return json.dumps({
        "status": "ok",
        "cleared_count": len(cleared),
        "cleared": cleared,
    }, indent=2)


# -------------------------------------------------------------------
# Session management
# -------------------------------------------------------------------

@mcp.tool(
    name="repl_list_sessions",
    annotations={
        "title": "List Active Sessions",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_list_sessions() -> str:
    """List all active REPL sessions.

    Returns:
        JSON array of sessions with IDs, timestamps, and variable counts.
    """
    return json.dumps({
        "status": "ok",
        "total": _sessions.count,
        "sessions": _sessions.list_sessions(),
    }, indent=2)


@mcp.tool(
    name="repl_delete_session",
    annotations={
        "title": "Delete Session",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_delete_session(params: SessionIdInput) -> str:
    """Delete a session and all its data.

    Returns:
        Confirmation message.
    """
    deleted = _sessions.delete_session(params.session_id)
    return json.dumps({
        "status": "ok" if deleted else "error",
        "message": "Session deleted." if deleted else f"Session '{params.session_id}' not found.",
    })


# -------------------------------------------------------------------
# Debug / introspection
# -------------------------------------------------------------------

@mcp.tool(
    name="repl_get_history",
    annotations={
        "title": "Get Execution History",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_get_history(params: HistoryInput) -> str:
    """Get the last N execution records for a session.

    Useful for debugging what happened in previous calls.

    Returns:
        JSON array of execution records.
    """
    session = _sessions.get_session(params.session_id)
    if session is None:
        return json.dumps({"status": "error", "message": f"Session '{params.session_id}' not found."})

    entries = session.history[-params.last_n:]
    return json.dumps({
        "status": "ok",
        "total_entries": len(session.history),
        "returned": len(entries),
        "entries": [asdict(e) for e in entries],
    }, indent=2, default=str)


@mcp.tool(
    name="repl_server_status",
    annotations={
        "title": "Server Status",
        "readOnlyHint": True,
        "destructiveHint": False,
        "idempotentHint": True,
        "openWorldHint": False,
    },
)
def repl_server_status() -> str:
    """Get current server status and configuration.

    Returns:
        JSON with Python version, session count, configuration, and limits.
    """
    return json.dumps({
        "status": "ok",
        "python_version": sys.version,
        "active_sessions": _sessions.count,
        "config": {
            "timeout_seconds": _config.timeout_seconds,
            "max_sessions": _config.max_sessions,
            "session_ttl_minutes": _config.session_ttl_minutes,
            "sandbox_enabled": _config.sandbox_enabled,
            "max_output_bytes": _config.max_output_bytes,
            "transport": _config.transport,
        },
        "working_directory": os.getcwd(),
    }, indent=2)


# ===================================================================
# Helpers
# ===================================================================

def _is_command_available(cmd: str) -> bool:
    try:
        subprocess.run(  # noqa: S603, S607
            [cmd, "--version"],
            capture_output=True,
            timeout=3,
        )
        return True
    except Exception:
        return False


# ===================================================================
# Entry point
# ===================================================================

def main() -> None:
    """CLI entry point for mcp-python-repl."""
    print(
        f"[mcp-python-repl] Starting (transport={_config.transport}, "
        f"sandbox={'ON' if _config.sandbox_enabled else 'OFF'}, "
        f"timeout={_config.timeout_seconds}s)",
        file=sys.stderr,
    )

    if _config.transport in ("streamable-http", "http", "sse"):
        mcp.run(transport="streamable-http", host=_config.host, port=_config.port)
    else:
        mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
