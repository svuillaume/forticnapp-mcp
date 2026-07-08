# HTTP MCP transport + Docker packaging

Status: implemented (formal spec-review/plan gate skipped per user request — prototype scope)
Date: 2026-07-07

## Context

`forticnapp-mcp` currently speaks MCP only over stdio (`main.py::run()`), spawned as a local
subprocess per Claude Desktop/Code config (`.mcp.json`). This works for single-user local usage
but doesn't support a shared, remotely-reachable deployment.

## Goal

Add an HTTP transport, packaged in Docker, so the server can run as one long-lived containerized
process that multiple MCP clients connect to over the network — without breaking or duplicating
the existing stdio path.

## Non-goals

- Multi-tenant FortiCNAPP credentials (per-client keyId/secret). This deployment continues to
  serve exactly one FortiCNAPP account per running instance, configured via env vars, same as
  today's stdio model.
- OAuth/external IdP integration for inbound auth. A static bearer token is sufficient for this
  deployment's trust model (a small number of known internal MCP clients).
- Horizontal session affinity / sticky load balancing. The HTTP transport is stateless by design
  (see below), so this is a non-problem rather than a deferred feature.

## Architecture

### 1. Shared core (refactor `main.py`)

Extract everything transport-agnostic out of `_serve()` into a new function:

```python
def build_mcp_server(settings: Settings) -> tuple[Server, ForticnappHttpClient]:
    ...  # load spec, discover token op, build auth strategy, build ToolRegistry,
         # build Server with list_tools/call_tool handlers registered
```

`main.py::run()` (stdio) becomes: load settings → `build_mcp_server()` → `stdio_server()` →
`server.run(...)` → `http_client.aclose()`. Behavior and CLI entrypoint (`forticnapp-mcp`) for
existing stdio users is unchanged.

### 2. New `http_server.py` + entrypoint

Wires the same `Server` returned by `build_mcp_server()` into the `mcp` SDK's
`StreamableHTTPSessionManager` (confirmed present in the already-pinned `mcp>=1.24.0,<2.0.0`
dependency — `mcp.server.streamable_http_manager.StreamableHTTPSessionManager`), mounted on a
`Starlette` app. `starlette`, `uvicorn`, and `sse-starlette` are already direct dependencies of
`mcp` (confirmed via `pip show mcp`), so **no new dependencies** are added to `pyproject.toml`.

Routes:
- `POST/GET/DELETE /mcp` — handled by `StreamableHTTPSessionManager`, constructed with
  `stateless=True` (each request is self-contained; no session store needed, so the container can
  restart or scale to multiple replicas behind a load balancer with no shared state).
- `GET /healthz` — plain 200 OK, no auth required, used by the Docker `HEALTHCHECK`.

Bearer-token middleware wraps `/mcp` only (not `/healthz`):
- Compares the `Authorization: Bearer <token>` header against `FORTICNAPP_MCP_HTTP_TOKEN` using
  `hmac.compare_digest` (timing-safe).
- Missing or mismatched token → `401` with a `WWW-Authenticate: Bearer` header, before any MCP/
  FortiCNAPP logic runs.

DNS-rebinding protection (`mcp.server.transport_security.TransportSecuritySettings`) is left
**disabled by default** — its `allowed_hosts`/`allowed_origins` allow-listing is aimed at
browser-based clients making credentialed cross-origin requests, which doesn't match this
deployment's client population (backend/API MCP clients presenting a bearer token). It's exposed
as an opt-in config knob for deployments that want it.

Entrypoint: `uvicorn.run(app, host=FORTICNAPP_MCP_HTTP_HOST, port=FORTICNAPP_MCP_HTTP_PORT)`,
registered as a new console script `forticnapp-mcp-http` in `pyproject.toml`.

### 3. Configuration

New env vars, read only by `http_server.py` (not added to the shared `Settings` model used by
stdio, so stdio mode's config surface and validation are unaffected):

| Var | Required | Default | Purpose |
|---|---|---|---|
| `FORTICNAPP_MCP_HTTP_TOKEN` | yes | — | Shared secret HTTP clients must present as a bearer token |
| `FORTICNAPP_MCP_HTTP_HOST` | no | `0.0.0.0` | Bind host |
| `FORTICNAPP_MCP_HTTP_PORT` | no | `8000` | Bind port |
| `FORTICNAPP_MCP_HTTP_ENABLE_DNS_REBINDING_PROTECTION` | no | `false` | Opt-in `TransportSecuritySettings` |
| `FORTICNAPP_MCP_HTTP_ALLOWED_HOSTS` | no | empty | Comma-separated, only used if the above is `true` |

All existing `FORTICNAPP_*` FortiCNAPP-account config (base URL, key id, secret, auth mode, etc.)
is unchanged and shared between both transports.

### 4. Error handling

- Bearer-auth failures return HTTP 401 before the MCP layer or FortiCNAPP API is touched.
- Everything downstream of a successful auth check is unchanged: `http_client.py::execute()`
  still never raises, still returns failures inside `ToolCallResult.error` — a bad/failing
  FortiCNAPP call looks identical over HTTP and stdio.
- Startup failures (missing `FORTICNAPP_MCP_HTTP_TOKEN`, port already in use, bad FortiCNAPP
  config) fail fast with a one-line stderr message, same convention as `main.py::_fail()`.

### 5. Docker packaging

`Dockerfile`:
- `python:3.11-slim` base
- non-root user
- `pip install .` (production deps only, no `[dev]` extra)
- `COPY lw.yaml` into the image (default spec path)
- `EXPOSE 8000`
- `HEALTHCHECK` hitting `GET /healthz`
- `CMD ["forticnapp-mcp-http"]`

`docker-compose.yml`:
- One service, `env_file: .env`, port mapping `8000:8000`, `restart: unless-stopped`.

README gets a new "Run in Docker" section documenting `docker compose up`, alongside the existing
local-install/stdio instructions (which stay as the primary documented path for Claude
Desktop/Code local use).

### 6. Testing

- Unit tests for the bearer-auth middleware: valid token → request proceeds; missing token → 401;
  wrong token → 401.
- A smoke test that `build_mcp_server()` produces an identical tool list regardless of which
  entrypoint calls it (proves the stdio/HTTP split didn't fork behavior).
- No changes needed to existing `tool_registry`/`http_client` tests — that layer is untouched by
  this work.

## Open questions / risks

- `mcp` SDK version is pinned `>=1.24.0,<2.0.0`; `StreamableHTTPSessionManager`'s constructor
  signature was verified against the currently-installed `1.28.1` — if a future `1.x` bump changes
  that signature, the implementation will need a small adjustment (low risk within the same major
  version).
- Single shared FortiCNAPP tenant means every HTTP client sees the same account's data. If
  per-team credential isolation is ever needed, that's a larger follow-up design (multi-tenant
  credential handling in `auth.py`/`http_client.py`), explicitly out of scope here.
