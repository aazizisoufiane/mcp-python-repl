"""Tests for mcp-python-repl core components."""

from __future__ import annotations

import json

import pytest

from mcp_python_repl.config import Config
from mcp_python_repl.executor import execute_code
from mcp_python_repl.session import Session, SessionManager


# ===================================================================
# Config
# ===================================================================

class TestConfig:
    def test_defaults(self):
        c = Config()
        assert c.timeout_seconds == 30
        assert c.sandbox_enabled is False

    def test_from_env(self, monkeypatch):
        monkeypatch.setenv("REPL_TIMEOUT", "10")
        monkeypatch.setenv("REPL_SANDBOX", "true")
        c = Config.from_env()
        assert c.timeout_seconds == 10
        assert c.sandbox_enabled is True


# ===================================================================
# Session
# ===================================================================

class TestSessionManager:
    def test_create_and_get(self):
        mgr = SessionManager(Config())
        s = mgr.create_session()
        assert mgr.get_session(s.session_id) is s

    def test_get_missing(self):
        mgr = SessionManager(Config())
        assert mgr.get_session("nonexistent") is None

    def test_get_or_create_new(self):
        mgr = SessionManager(Config())
        s = mgr.get_or_create(None)
        assert s.session_id

    def test_get_or_create_existing(self):
        mgr = SessionManager(Config())
        s1 = mgr.create_session()
        s2 = mgr.get_or_create(s1.session_id)
        assert s1.session_id == s2.session_id

    def test_delete(self):
        mgr = SessionManager(Config())
        s = mgr.create_session()
        assert mgr.delete_session(s.session_id) is True
        assert mgr.get_session(s.session_id) is None

    def test_max_sessions_evicts_oldest(self):
        cfg = Config(max_sessions=2)
        mgr = SessionManager(cfg)
        s1 = mgr.create_session()
        s2 = mgr.create_session()
        s3 = mgr.create_session()
        # s1 should have been evicted
        assert mgr.get_session(s1.session_id) is None
        assert mgr.get_session(s2.session_id) is not None
        assert mgr.get_session(s3.session_id) is not None

    def test_list_sessions(self):
        mgr = SessionManager(Config())
        mgr.create_session()
        mgr.create_session()
        listing = mgr.list_sessions()
        assert len(listing) == 2
        assert "session_id" in listing[0]


# ===================================================================
# Executor
# ===================================================================

class TestExecutor:
    @pytest.fixture()
    def session(self):
        from datetime import datetime, timezone
        return Session(
            session_id="test123",
            created_at=datetime.now(timezone.utc),
            last_used=datetime.now(timezone.utc),
        )

    @pytest.fixture()
    def config(self):
        return Config(timeout_seconds=5)

    def test_simple_expression(self, session, config):
        res = execute_code("result = 2 + 2", session, config)
        assert res.status == "completed"
        assert res.result == 4

    def test_variable_persistence(self, session, config):
        execute_code("x = 42", session, config)
        assert "x" in session.namespace
        assert session.namespace["x"] == 42

        res = execute_code("result = x * 2", session, config)
        assert res.result == 84

    def test_stdout_capture(self, session, config):
        res = execute_code("print('hello world')", session, config)
        assert res.status == "completed"
        assert "hello world" in res.stdout

    def test_syntax_error(self, session, config):
        res = execute_code("def foo(", session, config)
        assert res.status == "error"
        assert res.error_type == "SyntaxError"

    def test_name_error(self, session, config):
        res = execute_code("print(undefined_var)", session, config)
        assert res.status == "error"
        assert res.error_type == "NameError"
        assert res.hint is not None

    def test_runtime_error(self, session, config):
        res = execute_code("1 / 0", session, config)
        assert res.status == "error"
        assert res.error_type == "ZeroDivisionError"

    def test_empty_code(self, session, config):
        res = execute_code("", session, config)
        assert res.status == "error"

    def test_new_and_modified_vars(self, session, config):
        res1 = execute_code("a = 1; b = 2", session, config)
        assert "a" in (res1.new_vars or [])
        assert "b" in (res1.new_vars or [])

        res2 = execute_code("a = 10", session, config)
        assert "a" in (res2.modified_vars or [])

    def test_result_does_not_persist(self, session, config):
        execute_code("result = 'hello'", session, config)
        assert "result" not in session.namespace

    def test_sandbox_blocks_subprocess(self, session):
        cfg = Config(sandbox_enabled=True, timeout_seconds=5)
        res = execute_code("import subprocess", session, cfg)
        assert res.status == "error"
        assert "blocked" in (res.error_message or "").lower() or "ImportError" in (res.error_type or "")

    def test_sandbox_blocks_os_system(self, session):
        cfg = Config(sandbox_enabled=True, timeout_seconds=5)
        res = execute_code("import socket", session, cfg)
        assert res.status == "error"

    def test_history_recorded(self, session, config):
        execute_code("x = 1", session, config)
        execute_code("y = 2", session, config)
        assert len(session.history) == 2
        assert session.history[0].status == "completed"


# ===================================================================
# Integration: JSON output format
# ===================================================================

class TestJSONOutput:
    def test_run_code_returns_valid_json(self):
        from mcp_python_repl.server import _result_to_json
        from mcp_python_repl.executor import ExecutionResult

        res = ExecutionResult(
            status="completed",
            result={"key": "value"},
            new_vars=["data"],
        )
        output = _result_to_json(res, "sess1", ["data"])
        parsed = json.loads(output)
        assert parsed["status"] == "completed"
        assert parsed["session_id"] == "sess1"
        assert parsed["result"] == {"key": "value"}
        assert parsed["namespace"]["total"] == 1
