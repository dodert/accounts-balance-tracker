"""Which proxies the image trusts: the default of FORWARDED_ALLOW_IPS in the Dockerfile, checked with
uvicorn's own proxy headers middleware (the one `--proxy-headers` turns on)."""
import asyncio
import re
from pathlib import Path

from uvicorn.middleware.proxy_headers import ProxyHeadersMiddleware

DOCKERFILE = Path(__file__).parent.parent / "Dockerfile"


def image_default() -> str:
    match = re.search(r"FORWARDED_ALLOW_IPS=(\S+)", DOCKERFILE.read_text())
    assert match, "FORWARDED_ALLOW_IPS has no default in the Dockerfile"
    return match.group(1)


def seen_by_app(peer: str, forwarded_for: str | None = None, proto: str | None = None) -> tuple[str, str]:
    """The client address and scheme the app gets for a request from `peer` with these headers."""
    seen = {}

    async def app(scope, receive, send):
        seen["client"], seen["scheme"] = scope["client"][0], scope["scheme"]

    headers = []
    if forwarded_for:
        headers.append((b"x-forwarded-for", forwarded_for.encode()))
    if proto:
        headers.append((b"x-forwarded-proto", proto.encode()))
    scope = {"type": "http", "scheme": "http", "client": (peer, 1234), "headers": headers}
    asyncio.run(ProxyHeadersMiddleware(app, trusted_hosts=image_default())(scope, None, None))
    return seen["client"], seen["scheme"]


def test_the_image_does_not_trust_every_client():
    cmd = DOCKERFILE.read_text()
    assert "--proxy-headers" in cmd
    assert "--forwarded-allow-ips" not in cmd  # uvicorn reads FORWARDED_ALLOW_IPS instead


def test_a_client_from_the_internet_cannot_fake_its_ip_or_https():
    assert seen_by_app("203.0.113.9", "10.0.0.1", "https") == ("203.0.113.9", "http")


def test_a_proxy_on_a_docker_network_or_the_lan_passes_the_real_client_ip():
    for proxy in ("172.18.0.5", "192.168.1.20", "10.1.2.3", "127.0.0.1"):
        assert seen_by_app(proxy, "203.0.113.9", "https") == ("203.0.113.9", "https")


def test_an_ip_made_up_by_the_client_before_the_proxy_is_ignored():
    # The proxy adds the real address at the end; what the client wrote before it is not trusted
    assert seen_by_app("172.18.0.5", "1.2.3.4, 203.0.113.9")[0] == "203.0.113.9"
