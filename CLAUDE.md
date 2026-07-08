# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project

An MCP (Model Context Protocol) server that exposes FortiCNAPP (formerly Lacework) API 2.0 operations
as typed, auth-aware MCP tools over stdio. Tools are not hand-written: they are generated at startup
by parsing the OpenAPI spec (`lw.yaml`) and turning selected operations into pydantic-validated tool
schemas. See `README.md` for install/run/Claude Desktop config instructions — this file focuses on
architecture and spec quirks that aren't obvious from reading a single file.

**Build status**: complete. All modules in `src/forticnapp_mcp/` are implemented and have been
verified end-to-end, including a real stdio subprocess handshake (`initialize` → `tools/list` → 37
tools → `tools/call` → structured result) against the real `lw.yaml`.

## Commands

```bash
# install (editable, with dev deps)
pip install -e ".[dev]"

# run the server (stdio transport)
forticnapp-mcp
# equivalently:
python -m forticnapp_mcp.main

# lint
ruff check src/

# tests (pytest + pytest-asyncio + respx are declared as dev deps; no test files exist yet)
pytest
```

Python 3.11+ is required (`pyproject.toml` `requires-python`). There's no Makefile or CI config in
this repo — the commands above are the only entry points.

## Architecture

Startup sequence (`main.py::_serve`): load env/config → validate → load `lw.yaml` → inspect security
(`discover_token_operation`) → build auth strategy → extract/select operations → build tool registry →
run stdio MCP server. Module boundaries are one-way: `config` → `openapi_loader` → `auth` →
`http_client` → `tool_registry` → `main`. `errors.py`, `logging_utils.py`, `models.py`, and `utils.py`
are shared leaves with no dependencies on the others.

- **`models.py`** — `OperationSpec`/`OperationParameter` (dataclasses: internal metadata for one
  OpenAPI operation, including its generated pydantic `input_model`) and `ToolCallResult`/`RequestMeta`/
  `PaginationInfo` (pydantic: the structured JSON envelope every tool returns — `success`, `status_code`,
  `operation_id`, `request` (metadata only, never headers/secrets), `data`, `pagination`, `error`).
- **`errors.py`** — one exception hierarchy (`AuthError`, `ValidationError`, `ApiError`, `NetworkError`,
  `SpecError`) all deriving from `ForticnappError`, each carrying `category`/`status_code`/
  `operation_id`/`retryable` and a `to_dict()` used to fill `ToolCallResult.error`.
- **`logging_utils.py`** — structured JSON logging to **stderr** (stdout is reserved for the MCP
  JSON-RPC stdio transport). `redact_headers()`/`redact_secret()` must wrap any header dict or secret
  before it reaches a log call — this is enforced by convention, not by a lint rule, so check it in review.
- **`openapi_loader.py`** — loads `lw.yaml` (or `.json`), resolves `$ref` and merges `allOf`
  (225 schemas in this spec use `allOf`), and walks `paths` to produce a `list[OperationSpec]` via
  `extract_operations()`/`select_operations()`. Also has `discover_token_operation()`, which infers the
  token endpoint's field names from the spec (with fallback defaults) rather than hardcoding them.
- **`auth.py`** — three strategies (`ApiKeyAuthStrategy`, `BearerTokenStrategy`,
  `ApiKeyToTokenStrategy`) selected by `FORTICNAPP_AUTH_MODE`. Caches the bearer token in memory keyed
  off `expiresAt`, refreshes proactively and on 401. `ApiKeyToTokenStrategy._acquire_token` is the one
  documented customization point if a deployment's token contract differs (see README).
- **`http_client.py`** — wraps `httpx.AsyncClient`; builds requests from an `OperationSpec` +
  validated arguments, retries network/5xx errors with backoff, retries once on 401 after invalidating
  the cached token, and follows FortiCNAPP's cursor-style pagination. `execute()` never raises — every
  failure mode is captured into `ToolCallResult.error`. Also enforces `MAX_RESPONSE_BYTES` (default 5MB):
  a broad, unfiltered `Inventory/search` (or any other endpoint) can return tens of MB in one response,
  which overflows an MCP client's stdio message-size guard and kills the whole server connection. Rather
  than just erroring, an oversized response matching FortiCNAPP's list envelope
  (`{"paging": {...}, "data": [...]}`) is sliced into MCP-sized chunks (`_build_oversized_result`):
  continuation reuses the existing `page_url` contract via a stateless synthetic `LOCALPAGE:<base64>`
  cursor that re-encodes the original request + a row offset (the server holds no session state, so a
  follow-up call replays the identical upstream request and slices from there — real upstream `nextPage`
  URLs are used once local rows are exhausted). Only a shape that can't be sliced (not a list envelope,
  or even one row too large) still falls back to a hard `response_too_large` tool error.
- **`tool_registry.py`** — resolves tool-name collisions (see below), turns each `OperationSpec` into
  an `mcp.types.Tool` (`inputSchema` = `input_model.model_json_schema()`), and dispatches `call_tool`
  requests back to `http_client`, returning a plain `dict` (the low-level `mcp` SDK auto-populates both
  `content` and `structuredContent` from a dict return — no manual `TextContent` wrapping needed).
- **`main.py`** — wires all of the above into `mcp.server.lowlevel.Server` + `stdio_server`. Verified
  end-to-end via a real stdio subprocess handshake against `lw.yaml` (37 tools registered with the
  default `FORTICNAPP_ENABLED_TAGS`).

### Non-obvious facts about `lw.yaml` that drive the design

These were confirmed by directly inspecting the spec, not assumed — re-check them there before
changing related code:

- **No `components.securitySchemes` at all.** Auth is only described in prose/parameters, so it is
  hardcoded rather than derived generically. The real handshake: `POST /api/v2/access/tokens` with
  header `X-LW-UAKS: <secret>` and body `{"keyId": "...", "expiryTime": <=86400}`, returning
  `{"token": "...", "expiresAt": "<RFC3339>"}`. All other calls use `Authorization: Bearer <token>`.
  The credential is **two parts** (`FORTICNAPP_KEY_ID` + `FORTICNAPP_API_SECRET`), not one opaque key.
- **Zero `operationId`s anywhere** in the 120 operations — tool names must be derived from
  method + path. Paths use PascalCase resource segments (e.g. `/api/v2/AgentAccessTokens/{id}`).
- **No `servers` block** — the base URL is 100% env-driven (`FORTICNAPP_API_BASE_URL`, account-specific,
  e.g. `https://<account>.lacework.net`, with regional variants like `.fra.lacework.net`).
- **`GET` = safe, `POST` = mutation is wrong here.** Read-heavy listing is often `POST .../search`
  (query-by-body). Mutation classification is: GET → safe; POST ending in `/search` → safe;
  everything else (POST/PUT/PATCH/DELETE) → mutation, gated by `ENABLE_MUTATION_TOOLS`.
- **Pagination is cursor-by-URL**, not page/limit: response envelopes carry `paging.rows`,
  `paging.totalRows`, `paging.urls.nextPage` (a full absolute URL, valid 24h) — follow that URL
  directly rather than computing page numbers.
- **120 operations across 40 tags is too many tools by default.** `FORTICNAPP_ENABLED_TAGS` (see
  `.env.example`) curates a read-heavy default subset (`Alerts, Entities, Vulnerabilities,
  VulnerabilityExceptions, Inventory, Policies, Reports, CloudAccounts, Events`) to keep the tool list
  small enough for an LLM client to select from reliably; widen it deliberately, don't default to all.
- **Tool name collisions are possible**: e.g. `GET /api/v2/CloudAccounts/{type}` and
  `GET /api/v2/CloudAccounts/{intgGuid}` both naively derive to the same name. `tool_registry.py`'s
  `_resolve_name_collisions()` detects this (grouping by initially-derived name) and rebuilds colliding
  names with the differing path-parameter name as a disambiguator (e.g. `..._by_type_get` /
  `..._by_intg_guid_get`), falling back to a logged numeric suffix if that still collides.
