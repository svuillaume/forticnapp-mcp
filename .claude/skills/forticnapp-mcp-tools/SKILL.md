---
name: forticnapp-mcp-tools
description: Use when calling any mcp__forticnapp__* tool (FortiCNAPP/Lacework API) — covers loading deferred tool schemas, building filters/search bodies, dataset vs resourceType confusion on Inventory search, and avoiding oversized-response errors.
---

# FortiCNAPP MCP Tools

## Overview

The `mcp__forticnapp__*` tools are generated from `lw.yaml` (FortiCNAPP/Lacework API 2.0) and
are typically deferred — load a tool's schema with `ToolSearch` (`select:<tool_name>`, comma-join
multiple) before calling it. Two things trip up first calls: (1) guessing `Inventory/search`'s
`dataset` param as a resource-type selector, and (2) guessing filter operator names instead of
using the real enum.

## Search-endpoint request shape

Every `*_search` tool takes the same body shape:

```json
{
  "filters": [{"field": "resourceRegion", "expression": "ne", "value": "eu-west-1"}],
  "returns": ["resourceId", "resourceRegion"],
  "timeFilter": {"startTime": "2026-07-01T00:00:00Z", "endTime": "2026-07-07T00:00:00Z"},
  "page_url": null
}
```

- `filters`: list of `{field, expression, value|values}`. Valid `expression` values (verified
  against `lw.yaml`'s `StringFilterExpression`/etc. enums — don't guess others):
  `eq, ne, in, not_in, like, ilike, not_like, not_ilike, rlike, not_rlike, gt, ge, lt, le, between`.
  There is no `not_eq` — negation is `ne`. `in`/`not_in`/`between` use `values` (plural), not `value`.
- `returns`: restrict the field list. Always set this on wide entities/inventory rows — the raw
  objects (e.g. an EC2 `resourceConfig`) are huge and will blow the response-size limit.
- `timeFilter`: most entity/event/alert search tools default to the last 24h and cap at a 7-day
  range if you omit it or ask for more.
- `page_url`: pass the value from a previous response's `pagination.next_page_url` /
  `data.paging.urls.nextPage` to get the next page. Don't hand-construct page numbers.

## Inventory search: use `resourceType`, not `dataset`

`forticnapp_inventory_search` has both a `dataset` param and a `filters` param. `dataset` on this
endpoint is legacy/restricted to compliance-report datasets (e.g. `AwsCompliance`) — passing a
resource-type-looking string like `AwsEc2Instance` is **silently ignored**, not rejected, so you
get the entire unfiltered inventory back (hundreds of thousands of rows) instead of an error.

To scope to a resource type, add a `filters` entry instead:

```json
{"csp": "AWS", "filters": [{"field": "resourceType", "expression": "eq", "value": "ec2:instance"}]}
```

Resource-type values are lowercase `service:resource` strings (e.g. `ec2:instance`), not PascalCase
guesses. If unsure of the exact string, run one broad query with a tight `returns` (just
`resourceType`) to sample real values before filtering.

## Inventory rows are per-collection-snapshot, not per-resource

`paging.totalRows` on `Inventory/search` counts collection-snapshot rows, not distinct resources —
the same resource can appear many times across collection runs inside your `timeFilter` window
(e.g. 308 rows for 22 actual S3 buckets). Dedupe by `resourceId` (or `urn`) client-side before
reporting a count, or narrow `timeFilter` to a single recent window if you want one snapshot per
resource instead.

## Oversized responses now auto-chunk — don't manually re-narrow first

When a list/search response exceeds the server's byte limit, it no longer just errors: the server
slices it into a smaller chunk and returns `pagination.next_page_url` (an opaque cursor, not a
real FortiCNAPP URL) alongside `pagination.has_more: true`. Treat this exactly like normal
pagination — call the same tool again with only `page_url` set to that value to get the next
chunk; every other argument is ignored on that follow-up call, same as real upstream pagination.
Keep following `next_page_url` until `has_more` is `false`.

A genuine `response_too_large` error (data returned, `success: false`) now only happens when even
a single row can't fit safely, or the response shape isn't a `{"paging", "data": [...]}` list (e.g.
a single-object detail response). In that case, narrow the request: (1) add a
`resourceType`/`eventType`-style filter to cut row count, (2) shrink `returns` to only the fields
you need, (3) tighten `timeFilter`.

Also note: each chunked follow-up call re-issues the original upstream query and discards
everything except the next slice (the server is stateless, no server-side cache) — for a very
large result set this means real repeated upstream API calls, so prefer narrowing the query up
front over paging through many chunks if you only need a subset of the data anyway.

## Quick reference

| Symptom | Cause | Fix |
|---|---|---|
| Tool call fails with "not found" / schema unknown | Tool is deferred | `ToolSearch(query="select:<tool_name>[,...]")` first |
| `400 Invalid format in request body` on a filter | Bad `expression` value (e.g. `not_eq`) | Use the verified enum above; negation is `ne` |
| `Inventory/search` returns huge/irrelevant rows despite a `dataset` guess | `dataset` is compliance-only, ignored otherwise | Filter on `resourceType` instead |
| `response_too_large` / token-limit error | Query matched too many/too-wide rows | Add a scoping filter, then shrink `returns`, then tighten `timeFilter` |
| Inventory row count looks way higher than expected resource count | `totalRows` counts snapshot rows, not distinct resources | Dedupe by `resourceId`/`urn`, or narrow `timeFilter` |
