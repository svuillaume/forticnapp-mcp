from pathlib import Path

import pytest
import respx
from httpx import Response

from forticnapp_mcp.auth import BearerTokenStrategy
from forticnapp_mcp.config import Settings
from forticnapp_mcp.http_client import ForticnappHttpClient, _encode_local_cursor
from forticnapp_mcp.openapi_loader import extract_operations, load_spec

BASE = "https://acct.lacework.net"
SPEC = Path(__file__).resolve().parent.parent / "lw.yaml"
SEARCH_OP = next(op for op in extract_operations(load_spec(SPEC)) if op.supports_pagination)


def _client(**overrides) -> ForticnappHttpClient:
    settings = Settings(
        forticnapp_api_base_url=BASE,
        forticnapp_api_secret="s",
        forticnapp_auth_mode="bearer_static",
        forticnapp_openapi_spec=SPEC,
        **overrides,
    )
    return ForticnappHttpClient(settings, BearerTokenStrategy(header_name="Authorization", token="tok"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "page_url",
    [
        "https://attacker.example/collect",
        f"{BASE}.attacker.example/collect",
        "http://acct.lacework.net/api/v2/Alerts",  # scheme downgrade
        "//attacker.example/collect",
    ],
)
async def test_foreign_page_url_is_rejected_without_sending_token(page_url):
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=Response(200, json={}))
        result = await _client().execute(SEARCH_OP, {"page_url": page_url})
    assert not route.called
    assert result.success is False
    assert result.error.category == "validation_error"


@pytest.mark.asyncio
async def test_forged_mutating_cursor_is_rejected_when_mutations_disabled():
    cursor = _encode_local_cursor("DELETE", "/api/v2/AlertChannels/abc", None, None, 0)
    with respx.mock(assert_all_called=False) as mock:
        route = mock.route().mock(return_value=Response(200, json={}))
        result = await _client().execute(SEARCH_OP, {"page_url": cursor})
    assert not route.called
    assert result.error.category == "validation_error"


@pytest.mark.asyncio
async def test_mutating_cursor_allowed_when_mutations_enabled():
    cursor = _encode_local_cursor("DELETE", "/api/v2/AlertChannels/abc", None, None, 0)
    with respx.mock(base_url=BASE) as mock:
        mock.delete("/api/v2/AlertChannels/abc").mock(return_value=Response(200, json={}))
        result = await _client(enable_mutation_tools=True).execute(SEARCH_OP, {"page_url": cursor})
    assert result.success is True


@pytest.mark.asyncio
async def test_same_host_next_page_url_still_works():
    with respx.mock(base_url=BASE) as mock:
        mock.get("/api/v2/Alerts").mock(return_value=Response(200, json={"data": []}))
        result = await _client().execute(SEARCH_OP, {"page_url": f"{BASE}/api/v2/Alerts?next=1"})
    assert result.success is True


@pytest.mark.asyncio
async def test_local_search_cursor_still_works():
    cursor = _encode_local_cursor("POST", "/api/v2/Alerts/search", None, {"filters": []}, 0)
    with respx.mock(base_url=BASE) as mock:
        mock.post("/api/v2/Alerts/search").mock(return_value=Response(200, json={"data": []}))
        result = await _client().execute(SEARCH_OP, {"page_url": cursor})
    assert result.success is True
