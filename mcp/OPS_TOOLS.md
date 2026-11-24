# ops-tools (optional MCP server)

`mcp/ops_tools.py` gives an agent typed tools for **OpenProject** (v3 REST API) and
**Jenkins**, so it does not need browser automation or hand-quoted `curl`. It is **not** part
of the default `.mcp.json`: the plugin works without it. Standard library only.

## Enable

1. Create the env file from the template and fill it (a backend with missing values
   offers no tools):
   ```bash
   mkdir -p ~/.config/context-garden
   cp mcp/ops.env.template ~/.config/context-garden/ops.env
   chmod 600 ~/.config/context-garden/ops.env
   ```
   `OPS_ENV_FILE` points to another location. Variables already in the environment win.
2. Register the server, once per user:
   ```bash
   claude mcp add -s user ops-tools -- python3 /path/to/context-garden/mcp/ops_tools.py
   ```
   Codex: add the same command under `[mcp_servers.ops-tools]` in `~/.codex/config.toml`.
   To ship it with a plugin build instead, add an `ops-tools` entry to `.mcp.json` next to
   `garden-tools`.

## Tools

| OpenProject | Jenkins |
|---|---|
| `op_show`, `op_comments`, `op_children`, `op_search` | `jenkins_jobs`, `jenkins_info`, `jenkins_builds`, `jenkins_status` |
| **writes:** `op_comment`, `op_status`, `op_assign`, `op_describe`, `op_create` | `jenkins_log`, `jenkins_wait`, `jenkins_queue`, `jenkins_nodes`, `jenkins_config`, `jenkins_artifacts` |
| | **writes:** `jenkins_build`, `jenkins_stop`, `jenkins_setconfig` |

Jobs are paths (`Folder/Sub/job`); a build is a number or `lastBuild`, `lastFailedBuild`,
`lastSuccessfulBuild`. `op_create` leaves the new ticket unassigned. `op_comment` sends no
notification mail. `OPS_READ_ONLY=1` removes every tool that writes.

## Command line

The same file works without MCP: `python3 mcp/ops_tools.py op show id=123`,
`python3 mcp/ops_tools.py jk log job=Folder/job build=lastFailedBuild lines=300`.
Run it without a tool name to list the tools.

## Notes

- Auth: OpenProject Basic `apikey:<key>`, Jenkins Basic `user:<api-token>` (no crumb needed).
- TLS verification is always on. On macOS python.org builds the system CAs are read from
  `/etc/ssl/cert.pem`; `SSL_CERT_FILE` overrides.
- Output is cut at 60,000 characters; use `lines` for long Jenkins logs.
