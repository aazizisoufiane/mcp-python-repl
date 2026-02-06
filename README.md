# 🐍 mcp-python-repl

A **production-grade** MCP server providing a persistent Python REPL with multi-session support, sandboxing, and timeout protection.

Built for LLM agents that need to execute Python code across multiple turns with **variables that persist between calls**.

## ✨ Features

| Feature | Description |
|---|---|
| **Multi-session** | Isolated sessions with unique IDs — run parallel workflows |
| **Persistent namespace** | Variables survive across calls within a session |
| **Timeout protection** | Configurable execution timeout (SIGALRM on Unix) |
| **Sandboxing** | Optional mode blocks dangerous modules (`subprocess`, `socket`, etc.) |
| **Package install** | Install pip packages on-the-fly (prefers `uv` for speed) |
| **File execution** | Run `.py` files inside the persistent session |
| **Dual transport** | stdio (local) and streamable-http (remote) |
| **Full introspection** | List variables, get history, check server status |
| **Env-based config** | All settings via `REPL_*` environment variables |

## 🚀 Quick Start

### With Claude Desktop / Cursor (stdio)

Add to your MCP config:

```json
{
  "mcpServers": {
    "python-repl": {
      "command": "uvx",
      "args": ["mcp-python-repl"]
    }
  }
}
```

### With uv (local dev)

```bash
# Clone and run
git clone https://github.com/soufiane-aazizi/mcp-python-repl.git
cd mcp-python-repl
uv run mcp-python-repl
```

### HTTP transport (remote / multi-client)

```bash
REPL_TRANSPORT=streamable-http REPL_PORT=8000 uv run mcp-python-repl
```

## 🛠️ Tools

### Code Execution

| Tool | Description |
|---|---|
| `repl_run_code` | Execute Python code with persistent namespace |
| `repl_run_file` | Execute a `.py` file in the session |
| `repl_install_package` | Install a pip package (uses `uv` if available) |

### Namespace Management

| Tool | Description |
|---|---|
| `repl_list_namespace` | List all variables in a session |
| `repl_get_variable` | Get the full value of a variable |
| `repl_set_variable` | Inject a variable from JSON |
| `repl_delete_variable` | Delete a specific variable |
| `repl_clear_namespace` | Clear all variables in a session |

### Session Management

| Tool | Description |
|---|---|
| `repl_list_sessions` | List all active sessions |
| `repl_delete_session` | Delete a session and its data |

### Debugging

| Tool | Description |
|---|---|
| `repl_get_history` | Get execution history for a session |
| `repl_server_status` | Server config, Python version, session count |

## 🔄 How Persistence Works

```
Call 1:  repl_run_code(code="data = [1,2,3]; total = sum(data); result = total")
         → returns: {"result": 6, "session_id": "a1b2c3d4e5f6", "new_variables": ["data", "total"]}

Call 2:  repl_run_code(code="doubled = [x*2 for x in data]; result = doubled", session_id="a1b2c3d4e5f6")
         → returns: {"result": [2,4,6], "new_variables": ["doubled"]}
```

> **Important:** The `result` variable is for returning output to the caller. It does **NOT** persist. Use named variables instead.

## ⚙️ Configuration

All settings are configurable via environment variables:

| Variable | Default | Description |
|---|---|---|
| `REPL_TIMEOUT` | `30` | Max execution time in seconds |
| `REPL_MAX_SESSIONS` | `50` | Maximum concurrent sessions |
| `REPL_SESSION_TTL` | `120` | Session expiry in minutes |
| `REPL_MAX_OUTPUT` | `1048576` | Max stdout/stderr capture (bytes) |
| `REPL_SANDBOX` | `false` | Enable sandboxing (`true`/`false`) |
| `REPL_TRANSPORT` | `stdio` | Transport: `stdio` or `streamable-http` |
| `REPL_HOST` | `127.0.0.1` | HTTP host (when using HTTP transport) |
| `REPL_PORT` | `8000` | HTTP port (when using HTTP transport) |
| `REPL_WORKDIR` | `cwd` | Working directory for executions |

### Sandbox Mode

When `REPL_SANDBOX=true`, the following modules are blocked:

`subprocess`, `shutil`, `ctypes`, `socket`, `http.server`, `xmlrpc`, `ftplib`, `smtplib`, `telnetlib`, `webbrowser`

And the following builtins are removed: `exec`, `eval`, `compile`, `__import__` (replaced with a restricted version).

## 🧪 Development

```bash
# Install dev dependencies
uv sync --extra dev

# Run tests
uv run pytest -v

# Lint
uv run ruff check src/ tests/

# Test with MCP Inspector
npx @modelcontextprotocol/inspector uv run mcp-python-repl
```

## 📦 Project Structure

```
mcp-python-repl/
├── src/mcp_python_repl/
│   ├── __init__.py       # Package metadata
│   ├── config.py         # Env-based configuration
│   ├── session.py        # Multi-session manager with TTL
│   ├── executor.py       # Python code executor (timeout + sandbox)
│   └── server.py         # MCP server with all tools
├── tests/
│   └── test_core.py      # Unit + integration tests
├── pyproject.toml        # uv/hatch project config
├── LICENSE               # MIT
└── README.md
```

## 📄 License

MIT — See [LICENSE](LICENSE).
