"""REST-poll connector tests (via respx)."""

import httpx
import respx

from lumid_data.sources.rest_poll import RestPollSpec, fetch


@respx.mock
def test_fetch_returns_status_body_headers() -> None:
    respx.get("https://example.com/api").mock(
        return_value=httpx.Response(
            200, content=b'{"ok": true}', headers={"x-foo": "bar"}
        )
    )
    spec = RestPollSpec(url="https://example.com/api")
    code, body, headers = fetch(spec)
    assert code == 200
    assert body == b'{"ok": true}'
    assert headers["x-foo"] == "bar"


@respx.mock
def test_fetch_passes_query_options() -> None:
    route = respx.get("https://example.com/api", params={"page": "2"}).mock(
        return_value=httpx.Response(200)
    )
    spec = RestPollSpec(url="https://example.com/api", options={"page": "2"})
    fetch(spec)
    assert route.called
