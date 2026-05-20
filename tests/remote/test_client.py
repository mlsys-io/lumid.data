"""RemoteMCPClient: prefixing, dispatch, result coercion.

The SSE transport is faked by replacing ``_session()`` with an async
context manager that yields a stub session — exercising the real MCP
SDK over the network belongs in tests/e2e/, not here.
"""

from contextlib import asynccontextmanager
from types import SimpleNamespace
from typing import Any

import pytest

from lumid_data.remote.client import RemoteMCPClient, _coerce_result


class _StubSession:
    """Minimal stand-in for ``mcp.ClientSession``."""

    def __init__(self) -> None:
        self.list_tools_result = SimpleNamespace(tools=[])
        self.call_tool_result: Any = SimpleNamespace(content=[], isError=False)
        self.calls: list[tuple[str, dict[str, Any]]] = []
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def list_tools(self) -> Any:
        return self.list_tools_result

    async def call_tool(self, name: str, arguments: dict[str, Any]) -> Any:
        self.calls.append((name, arguments))
        if isinstance(self.call_tool_result, Exception):
            raise self.call_tool_result
        return self.call_tool_result


def _bind_stub(client: RemoteMCPClient, session: _StubSession) -> None:
    """Replace ``client._session()`` with a CM yielding ``session``."""

    @asynccontextmanager
    async def _fake_session() -> Any:
        yield session

    client._session = _fake_session  # type: ignore[method-assign]


async def _open_with_stub(
    *, prefix: str = "remote_", tools: list[Any] | None = None
) -> tuple[RemoteMCPClient, _StubSession]:
    session = _StubSession()
    session.list_tools_result = SimpleNamespace(
        tools=tools
        or [
            SimpleNamespace(
                name="ohlc",
                description="bars",
                inputSchema={
                    "type": "object",
                    "properties": {"symbol": {"type": "string"}},
                },
            )
        ]
    )
    client = RemoteMCPClient(url="x://stub", token=None, prefix=prefix)
    _bind_stub(client, session)
    await client.aopen()
    return client, session


def test_owns_uses_prefix() -> None:
    client = RemoteMCPClient(url="x://stub", token=None, prefix="remote_")
    assert client.owns("remote_ohlc")
    assert client.owns("remote_anything")
    assert not client.owns("get_db")
    assert not client.owns("ohlc")


async def test_aopen_lists_tools_and_prefixes_them() -> None:
    client, _ = await _open_with_stub(
        prefix="src_",
        tools=[
            SimpleNamespace(name="a", description="d-a", inputSchema={}),
            SimpleNamespace(name="b", description=None, inputSchema=None),
        ],
    )
    assert client.opened
    names = sorted(t.name for t in client.tools())
    assert names == ["src_a", "src_b"]
    by_name = {t.name: t for t in client.tools()}
    assert by_name["src_b"].description == "Remote tool b"
    assert by_name["src_b"].input_schema == {"type": "object"}


async def test_call_when_not_open_returns_503() -> None:
    client = RemoteMCPClient(url="x://stub", token=None)
    _bind_stub(client, _StubSession())
    result = await client.call("remote_ohlc", {"symbol": "NVDA"})
    assert result["status_code"] == 503


async def test_call_for_unowned_tool_returns_400() -> None:
    client, _ = await _open_with_stub()
    result = await client.call("get_db", {"x": 1})
    assert result["status_code"] == 400


async def test_call_dispatches_unprefixed_name_to_session() -> None:
    client, session = await _open_with_stub()
    session.call_tool_result = SimpleNamespace(
        isError=False,
        content=[SimpleNamespace(text='{"row_count": 7}')],
    )
    result = await client.call("remote_ohlc", {"symbol": "NVDA", "interval": "1d"})
    assert session.calls == [("ohlc", {"symbol": "NVDA", "interval": "1d"})]
    assert result == {"status_code": 200, "body": {"row_count": 7}}


async def test_call_wraps_session_exception_as_502() -> None:
    client, session = await _open_with_stub()
    session.call_tool_result = RuntimeError("upstream broken")
    result = await client.call("remote_ohlc", {})
    assert result["status_code"] == 502
    assert "upstream broken" in result["body"]["error"]


def test_coerce_result_iserror_status_502() -> None:
    result = SimpleNamespace(isError=True, content=[SimpleNamespace(text="boom")])
    out = _coerce_result(result)
    assert out["status_code"] == 502
    assert out["body"] == "boom"


def test_coerce_result_empty_content() -> None:
    result = SimpleNamespace(isError=False, content=[])
    out = _coerce_result(result)
    assert out == {"status_code": 200, "body": None}


def test_coerce_result_non_text_block() -> None:
    class _Img(SimpleNamespace):
        def model_dump(self, mode: str = "json") -> dict[str, Any]:
            return {"type": "image", "data": "AAA"}

    result = SimpleNamespace(isError=False, content=[_Img(type="image")])
    out = _coerce_result(result)
    assert out["status_code"] == 200
    assert out["body"] == [{"type": "image", "data": "AAA"}]


async def test_aclose_marks_unopened() -> None:
    client, _ = await _open_with_stub()
    assert client.opened
    await client.aclose()
    assert not client.opened
    # subsequent call fails closed
    result = await client.call("remote_ohlc", {})
    assert result["status_code"] == 503


@pytest.mark.parametrize("token,expected_auth", [(None, False), ("abc", True)])
def test_token_becomes_authorization_header(
    token: str | None, expected_auth: bool
) -> None:
    c = RemoteMCPClient(url="x://stub", token=token)
    has_auth = "Authorization" in c._headers
    assert has_auth is expected_auth
    if token:
        assert c._headers["Authorization"] == f"Bearer {token}"
