# Deploying the FortiCNAPP MCP server

This is a deployment-focused guide. For architecture and design rationale, see `README.md` and
`CLAUDE.md`. There are two ways to run this server — pick based on who needs to reach it.

| | stdio | HTTP (Docker) |
|---|---|---|
| Entrypoint | `forticnapp-mcp` | `forticnapp-mcp-http` |
| Who connects | One local MCP client (Claude Desktop/Code) that spawns the process itself | Any number of MCP clients over the network |
| Process lifetime | Spawned per client session, dies with it | Long-lived, one shared instance |
| Inbound auth | None needed — process is local and client-spawned | Required: `FORTICNAPP_MCP_HTTP_TOKEN` bearer token |
| Typical host | Your laptop | A container host / internal service |

Both transports share one FortiCNAPP account per running instance (configured via the same
`FORTICNAPP_*` env vars) — there's no per-client credential isolation in either mode.

## Prerequisites

- A FortiCNAPP/Lacework account with an API key (Settings > API Keys in the console) — gives you
  a `keyId` and a `secret`, both required.
- Python 3.11+ for stdio / local installs. Docker for the HTTP deployment (no local Python needed
  in that case — it's all built into the image).

## Option A: stdio (local, single client)

Use this for Claude Desktop or Claude Code on your own machine — it's the simplest option and
what most users want. Both work the same way: the client spawns `forticnapp-mcp` as a subprocess
and talks MCP over its stdin/stdout, so there's no network exposure and no `FORTICNAPP_MCP_HTTP_*`
config involved at all.

First, install and configure once, then wire it into whichever client you use (below):

```bash
git clone <this repo> && cd mcp_forticnapp
pip install -e .
cp .env.example .env
# fill in FORTICNAPP_API_BASE_URL, FORTICNAPP_KEY_ID, FORTICNAPP_API_SECRET
```

`cwd` in both scenarios below must be this project's root so the default
`FORTICNAPP_OPENAPI_SPEC=./lw.yaml` resolves — or set an absolute path to `lw.yaml` instead. The
server validates config and loads the spec before it starts listening, and exits with a clear
one-line stderr message if either fails — it will not start half-configured, so a bad edit here
shows up immediately as a connection error in the client rather than a silent hang.

> **`.env` is the only source of truth for credentials — never put them in a client config file.**
> Settings already auto-loads `.env` from `cwd` (`pydantic-settings`), so as long as `cwd` points
> at this project and `.env` is filled in, the client config needs no `env` block at all, and
> should never have one added. Config JSON files (especially Claude Code's project-local
> `.mcp.json`) get synced, screenshotted, pasted into support tickets, and accidentally committed
> to git; `.env` (gitignored by `forticnapp-mcp-setup`, safe to `chmod 600`) isn't exposed to any
> of that by default. Neither scenario below has an inline-secret variant — if `.env` genuinely
> can't reach the process in your setup, fix that (e.g. fix `cwd`, or set
> `FORTICNAPP_OPENAPI_SPEC`/other paths absolute) rather than putting credentials in the client
> config.

### Scenario 1: Claude Desktop

1. Locate `claude_desktop_config.json`:
   - macOS: `~/Library/Application Support/Claude/claude_desktop_config.json`
   - Windows: `%APPDATA%\Claude\claude_desktop_config.json`
2. Add a `forticnapp` entry under `mcpServers` (create the file if it doesn't exist):

   ```json
   {
     "mcpServers": {
       "forticnapp": {
         "command": "/absolute/path/to/mcp_forticnapp/.venv/bin/python",
         "args": ["-m", "forticnapp_mcp.main"],
         "cwd": "/absolute/path/to/mcp_forticnapp"
       }
     }
   }
   ```

   No `env` block — credentials come from `.env` in `cwd` only. Pointing `command` at the venv
   interpreter directly (rather than bare `python`) avoids depending on whatever's first on
   Claude Desktop's `PATH`.
3. Fully quit and restart Claude Desktop (reloading the config from a running instance isn't
   enough).
4. Verify: open a new chat, click the 🔨 tools icon (or equivalent MCP indicator) — you should see
   `forticnapp` listed with ~37 tools (e.g. `forticnapp_alerts_list`, `forticnapp_inventory_search`).
   If it's missing, check Claude Desktop's MCP log (Settings > Developer, or
   `~/Library/Logs/Claude/mcp*.log` on macOS) for the stderr message the server printed on failure.

### Scenario 2: Claude Code

Claude Code reads the same kind of config from a project-local `.mcp.json` (or via the `claude mcp
add` CLI, which writes that file for you). From the project root, with `.env` already filled in:

```bash
claude mcp add forticnapp -- /absolute/path/to/mcp_forticnapp/.venv/bin/python -m forticnapp_mcp.main
```

Or edit `.mcp.json` directly (this is exactly what the command above generates):

```json
{
  "mcpServers": {
    "forticnapp": {
      "type": "stdio",
      "command": "/absolute/path/to/mcp_forticnapp/.venv/bin/python",
      "args": ["-m", "forticnapp_mcp.main"],
      "cwd": "/absolute/path/to/mcp_forticnapp"
    }
  }
}
```

`forticnapp-mcp-setup` (see Prerequisites) writes this file for you automatically if you'd rather
not hand-edit it, and also adds `.mcp.json`/`.env` to `.gitignore` — if you hand-create either
file instead, add that entry yourself so credentials (or a config pointing at them) never end up
committed.

Verify: run `claude mcp list` — `forticnapp` should show as connected. Then in a Claude Code
session, ask it something that requires a tool call (e.g. "list forticnapp alerts from the last
day") and confirm it invokes `mcp__forticnapp__forticnapp_alerts_list` rather than answering from
general knowledge. If the server fails to start, `claude mcp list` / the session's tool-call error
will surface the same one-line stderr message `_serve()` printed.

## Option B: HTTP in Docker (shared/remote)

Use this when more than one person or client needs to reach the same server over the network.

### 1. Configure

```bash
cp .env.example .env
```

Fill in the same FortiCNAPP credentials as above, plus the HTTP-only vars:

| Var | Required | Default | Notes |
|---|---|---|---|
| `FORTICNAPP_MCP_HTTP_TOKEN` | **yes** | — | Shared secret clients present as `Authorization: Bearer <token>`. Generate one with `openssl rand -hex 32`. This is separate from your FortiCNAPP credentials — it protects the HTTP endpoint itself. |
| `FORTICNAPP_MCP_HTTP_HOST` | no | `0.0.0.0` | Bind address inside the container. |
| `FORTICNAPP_MCP_HTTP_PORT` | no | `8000` | Bind port inside the container. |

### 2. Build and run

```bash
docker compose up --build
```

Or without compose:

```bash
docker build -t forticnapp-mcp .
docker run -d --name forticnapp-mcp --env-file .env -p 8000:8000 forticnapp-mcp
```

### 3. Verify

```bash
curl http://localhost:8000/healthz
# -> ok  (no auth required; this is what Docker's HEALTHCHECK polls)

curl -X POST http://localhost:8000/mcp/ \
  -H "Authorization: Bearer $FORTICNAPP_MCP_HTTP_TOKEN" \
  -H "Content-Type: application/json" \
  -H "Accept: application/json, text/event-stream" \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"curl","version":"0"}}}'
# -> an SSE event containing the server's initialize result
```

Point MCP clients at `http://<host>:8000/mcp/` (trailing slash; a bare `/mcp` 307-redirects
there, which well-behaved clients follow automatically) with the bearer token set.

### Production notes

- **TLS**: the container serves plain HTTP. Put a reverse proxy (nginx, Caddy, an ALB/cloud load
  balancer) in front to terminate TLS before this reaches the network — don't expose port 8000
  directly to untrusted networks.
- **Scaling**: the session manager runs `stateless=True` — each request is self-contained, so you
  can run multiple replicas behind a load balancer with no shared session store needed.
- **Secrets**: `FORTICNAPP_API_SECRET` and `FORTICNAPP_MCP_HTTP_TOKEN` are both read from the
  environment at startup and kept in memory only. Use your platform's secret injection (Docker
  secrets, Kubernetes Secrets, etc.) rather than baking them into the image or committing `.env`.
- **Rotating the HTTP token**: change `FORTICNAPP_MCP_HTTP_TOKEN` and restart the container —
  there's no separate revocation step since it's a single static shared secret, not an issued
  token with its own lifecycle.

## Response size & pagination

Every tool call is capped at `MAX_RESPONSE_BYTES` (default 5MB, same env var either transport) to
avoid overflowing an MCP client's message-size limit. This applies regardless of which option
above you're running — it's server-side behavior, not specific to stdio or Docker.

A broad, unfiltered query against a list/search endpoint (e.g. `Inventory/search` with no
`resourceType` narrowing) can genuinely return tens of MB from FortiCNAPP in one page. Rather than
just failing, the server auto-chunks any oversized response that matches FortiCNAPP's list shape:
it returns as many rows as fit, plus `pagination.next_page_url` and `pagination.has_more: true`.
Well-behaved MCP clients keep calling the same tool with only `page_url` set until `has_more` is
`false` — no different from following FortiCNAPP's own upstream pagination, which uses the same
fields.

**Cost to be aware of when deploying**: the server holds no session state (deliberately, per the
stateless design under Option B), so each chunked follow-up call re-issues the *entire* original
upstream query and discards everything except the next slice. A result set needing 10 chunks means
10 real calls to FortiCNAPP for that one logical request — factor this into rate-limit/cost
planning if you expect callers to routinely run broad, unfiltered queries. Narrowing `filters`/
`returns`/`timeFilter` up front avoids this entirely and is always cheaper than paging through many
chunks.

A response can still fail outright with `response_too_large` (`success: false`, no data) in two
narrower cases: the response isn't list-shaped (e.g. a single-object detail response too large on
its own) or even one row can't fit under the limit — both mean narrowing the query is the only fix.

## Troubleshooting

| Symptom | Cause | Fix |
|---|---|---|
| `forticnapp-mcp: configuration error: ...` on startup | Missing/invalid `FORTICNAPP_*` env var | Check the message — it lists exactly which field failed validation |
| `forticnapp-mcp: FORTICNAPP_MCP_HTTP_TOKEN is required...` | Running `forticnapp-mcp-http` without that var set | Set it in `.env` before `docker compose up` |
| `401` on every `/mcp` request | Missing/wrong `Authorization: Bearer` header | Confirm the token matches `FORTICNAPP_MCP_HTTP_TOKEN` exactly, no extra whitespace |
| `404` on `POST /mcp` (no trailing slash, redirect not followed) | Some HTTP clients/tools don't follow `307` on POST | Call `/mcp/` directly instead of `/mcp` |
| Docker build fails on `pip install .` | Build context missing `README.md` (pyproject.toml references it) | Build from the repo root so both files are present, as the provided `Dockerfile` does |
| `no operations selected` at startup | `FORTICNAPP_ENABLED_TAGS` doesn't match any tag in the loaded spec | Check the tag names against `lw.yaml`, or widen the list |
| `response_too_large` even though results look small | Response isn't list-shaped, or a single row alone exceeds `MAX_RESPONSE_BYTES` (rare) | Narrow `filters`/`returns`/`timeFilter`; auto-chunking can't help these two cases |
| Unexpectedly high FortiCNAPP API call volume from this server | A caller is paging through many auto-chunked responses on broad queries (see above) | Encourage narrower `filters`/`returns` on high-cardinality tools like `Inventory/search` |
