"""Interactive onboarding: collects FortiCNAPP credentials, validates them against
the real token-exchange endpoint, then writes .env, .mcp.json, and .gitignore so a
fresh clone goes from `pip install -e .` to a working Claude Code MCP connection.

Run from the project root: `forticnapp-mcp-setup`.
"""

from __future__ import annotations

import asyncio
import getpass
import json
import sys
from pathlib import Path

import httpx

from .auth import ApiKeyToTokenStrategy
from .errors import ForticnappError
from .models import TokenOperationHint

_ENV_KEYS = ("FORTICNAPP_API_BASE_URL", "FORTICNAPP_KEY_ID", "FORTICNAPP_API_SECRET")
_GITIGNORE_ENTRIES = (".env", ".venv/")
_MCP_SERVER_NAME = "forticnapp"
_MCP_SERVER_CONFIG = {
    "type": "stdio",
    "command": "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python",
    "args": ["-m", "forticnapp_mcp.main"],
    "env": {},
}


def main() -> None:
    project_root = Path.cwd()
    if not (project_root / "src" / "forticnapp_mcp").is_dir():
        print(
            "This doesn't look like the forticnapp-mcp project root "
            f"(no src/forticnapp_mcp under {project_root}). Run this from the repository root.",
            file=sys.stderr,
        )
        raise SystemExit(1)

    env_path = project_root / ".env"
    existing = _parse_env_file(env_path)

    print("FortiCNAPP MCP setup\n")
    base_url = _prompt("FortiCNAPP API base URL", default=existing.get("FORTICNAPP_API_BASE_URL", ""))
    key_id = _prompt("FortiCNAPP key ID", default=existing.get("FORTICNAPP_KEY_ID", ""))
    secret = _prompt_secret(existing.get("FORTICNAPP_API_SECRET", ""))

    if not base_url or not key_id or not secret:
        print("Base URL, key ID, and secret are all required.", file=sys.stderr)
        raise SystemExit(1)

    print("\nValidating credentials against the FortiCNAPP token endpoint...")
    try:
        asyncio.run(_validate_credentials(base_url, key_id, secret))
    except (ForticnappError, httpx.HTTPError) as exc:
        print(f"Validation failed: {exc}")
        if _prompt("Save the configuration anyway?", default="n").strip().lower() not in ("y", "yes"):
            raise SystemExit(1) from exc
    else:
        print("Credentials are valid.")

    _upsert_env_file(
        env_path,
        {
            "FORTICNAPP_API_BASE_URL": base_url,
            "FORTICNAPP_KEY_ID": key_id,
            "FORTICNAPP_API_SECRET": secret,
        },
    )
    _write_mcp_json(project_root / ".mcp.json")
    _ensure_gitignore(project_root / ".gitignore")

    print(
        "\nDone. .env, .mcp.json, and .gitignore are set up.\n"
        "Next: run `claude` in this directory, approve the 'forticnapp' MCP server "
        "when prompted, and check `/mcp` to confirm it's connected."
    )


async def _validate_credentials(base_url: str, key_id: str, secret: str) -> None:
    strategy = ApiKeyToTokenStrategy(
        token_url="/api/v2/access/tokens",
        key_id=key_id,
        secret=secret,
        expiry_seconds=3600,
        token_header_name="Authorization",
        bearer_prefix="Bearer",
        hint=TokenOperationHint(),
    )
    async with httpx.AsyncClient(base_url=base_url, timeout=15.0) as client:
        await strategy.get_headers(client)


def _prompt(label: str, *, default: str = "") -> str:
    suffix = f" [{default}]" if default else ""
    value = input(f"{label}{suffix}: ").strip()
    return value or default


def _prompt_secret(existing: str) -> str:
    suffix = " [press Enter to keep existing]" if existing else ""
    value = getpass.getpass(f"FortiCNAPP API secret{suffix}: ").strip()
    return value or existing


def _parse_env_file(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip()
    return values


def _upsert_env_file(path: Path, updates: dict[str, str]) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    remaining = dict(updates)
    for i, line in enumerate(lines):
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            lines[i] = f"{key}={remaining.pop(key)}"
    for key, value in remaining.items():
        lines.append(f"{key}={value}")
    path.write_text("\n".join(lines) + "\n")


def _write_mcp_json(path: Path) -> None:
    config = json.loads(path.read_text()) if path.exists() else {}
    config.setdefault("mcpServers", {})[_MCP_SERVER_NAME] = _MCP_SERVER_CONFIG
    path.write_text(json.dumps(config, indent=2) + "\n")


def _ensure_gitignore(path: Path) -> None:
    lines = path.read_text().splitlines() if path.exists() else []
    existing = set(lines)
    for entry in _GITIGNORE_ENTRIES:
        if entry not in existing:
            lines.append(entry)
    path.write_text("\n".join(lines) + "\n")


if __name__ == "__main__":
    main()
