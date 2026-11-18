# Installing the ctxprune MCP server (for AI agents)

ctxprune's MCP server is a local stdio server started with `uvx ctxprune mcp`. It needs `uv` (or `uvx`) and Python 3.9+.
It makes no network calls; the optional `read_pruned` tool downloads a 143 MB model from Hugging Face on first use.

1. Check that `uvx` is available: `uvx --version`. If it is missing, install uv first:
   `curl -LsSf https://astral.sh/uv/install.sh | sh` (macOS/Linux) or
   `powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"` (Windows).
2. Add this server to the client's MCP settings:

```json
{
  "mcpServers": {
    "ctxprune": {
      "command": "uvx",
      "args": ["ctxprune", "mcp"]
    }
  }
}
```

3. Restart or reload the MCP servers. Two tools appear:
   - `read_compact(path, start_line?, end_line?)`: read a log, JSON/MCP dump, CSV or test output through a lossless
     rewrite (repeat counts, JSON record lists as rows, colour codes removed). Nothing is dropped.
   - `read_pruned(path, keep=0.5, protect=true)`: lossy token pruning that keeps identifiers, numbers and paths.
4. Check it: ask the agent to call `read_compact` on any large log or JSON file. The last content block says how many
   characters were saved, e.g. `[ctxprune: 1,495 -> 708 chars, lossless]`.

No API keys or environment variables are needed. `CTXPRUNE_MCP_MAX_BYTES` (default 20 MB) caps the file size.
