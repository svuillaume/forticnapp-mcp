import json

import pytest
import respx
from httpx import Response

from forticnapp_mcp.errors import AuthError
from forticnapp_mcp.setup_cli import (
    _ensure_gitignore,
    _parse_env_file,
    _upsert_env_file,
    _validate_credentials,
    _write_mcp_json,
)


@pytest.mark.asyncio
async def test_validate_credentials_success():
    with respx.mock(base_url="https://acct.lacework.net") as mock:
        mock.post("/api/v2/access/tokens").mock(
            return_value=Response(201, json={"token": "tok", "expiresAt": "2026-01-01T00:00:00Z"})
        )
        await _validate_credentials("https://acct.lacework.net", "keyid", "secret")


@pytest.mark.asyncio
async def test_validate_credentials_failure():
    with respx.mock(base_url="https://acct.lacework.net") as mock:
        mock.post("/api/v2/access/tokens").mock(return_value=Response(401, json={"message": "bad creds"}))
        with pytest.raises(AuthError):
            await _validate_credentials("https://acct.lacework.net", "keyid", "wrong-secret")


def test_upsert_env_file_creates_new(tmp_path):
    path = tmp_path / ".env"
    _upsert_env_file(path, {"FORTICNAPP_API_BASE_URL": "https://a", "FORTICNAPP_KEY_ID": "k"})
    values = _parse_env_file(path)
    assert values["FORTICNAPP_API_BASE_URL"] == "https://a"
    assert values["FORTICNAPP_KEY_ID"] == "k"


def test_upsert_env_file_preserves_unrelated_lines(tmp_path):
    path = tmp_path / ".env"
    path.write_text("# comment\nFORTICNAPP_API_BASE_URL=https://old\nFORTICNAPP_ENABLED_TAGS=Alerts\n")

    _upsert_env_file(path, {"FORTICNAPP_API_BASE_URL": "https://new", "FORTICNAPP_KEY_ID": "k"})

    text = path.read_text()
    assert "# comment" in text
    assert "FORTICNAPP_ENABLED_TAGS=Alerts" in text
    values = _parse_env_file(path)
    assert values["FORTICNAPP_API_BASE_URL"] == "https://new"
    assert values["FORTICNAPP_KEY_ID"] == "k"


def test_write_mcp_json_creates_new(tmp_path):
    path = tmp_path / ".mcp.json"
    _write_mcp_json(path)
    config = json.loads(path.read_text())
    assert config["mcpServers"]["forticnapp"]["command"] == "${CLAUDE_PROJECT_DIR:-.}/.venv/bin/python"


def test_write_mcp_json_preserves_other_servers(tmp_path):
    path = tmp_path / ".mcp.json"
    path.write_text(json.dumps({"mcpServers": {"other": {"command": "other-cmd"}}}))

    _write_mcp_json(path)

    config = json.loads(path.read_text())
    assert config["mcpServers"]["other"]["command"] == "other-cmd"
    assert "forticnapp" in config["mcpServers"]


def test_ensure_gitignore_creates_new(tmp_path):
    path = tmp_path / ".gitignore"
    _ensure_gitignore(path)
    lines = path.read_text().splitlines()
    assert ".env" in lines
    assert ".venv/" in lines


def test_ensure_gitignore_is_idempotent(tmp_path):
    path = tmp_path / ".gitignore"
    path.write_text(".env\nnode_modules/\n")

    _ensure_gitignore(path)
    _ensure_gitignore(path)

    lines = path.read_text().splitlines()
    assert lines.count(".env") == 1
    assert lines.count(".venv/") == 1
    assert "node_modules/" in lines
