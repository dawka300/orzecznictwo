import unittest
from unittest.mock import MagicMock, patch

from judgments_core.cli import _make_source
from judgments_core.proxy_seller import (
    ProxySellerTransport,
    fetch_residential_proxies,
    get_proxy_seller_transport,
    has_positive_balance,
    parse_proxy_lines,
)
from judgments_core.settings import Settings
from judgments_core.transport import FallbackTransport


class FakeResponse:
    def __init__(self, status_code, content=b"{}", headers=None):
        self.status_code = status_code
        self.content = content
        self.headers = headers or {}
        self.url = "https://example.test"


class TestProxySeller(unittest.TestCase):
    def test_has_positive_balance(self):
        self.assertFalse(has_positive_balance(None))
        self.assertFalse(has_positive_balance(0))
        self.assertFalse(has_positive_balance(0.0))
        self.assertFalse(has_positive_balance(-1.5))
        self.assertFalse(has_positive_balance("$0.00"))
        self.assertFalse(has_positive_balance({"balance": 0}))
        self.assertFalse(has_positive_balance({"summ": "0.00"}))

        self.assertTrue(has_positive_balance(10))
        self.assertTrue(has_positive_balance(0.01))
        self.assertTrue(has_positive_balance("$25.00"))
        self.assertTrue(has_positive_balance({"balance": 15.5}))
        self.assertTrue(has_positive_balance({"data": {"balance": 5.0}}))

    def test_parse_proxy_lines(self):
        raw = """
        # Komentarz
        http://user:pass@res.proxy-seller.com:10001
        socks5://user:pass@res.proxy-seller.com:10002
        user:pass@res.proxy-seller.com:10003
        res.proxy-seller.com:10004:user:pass
        res.proxy-seller.com:10005
        """
        parsed = parse_proxy_lines(raw, default_protocol="http")
        self.assertEqual(len(parsed), 5)
        self.assertEqual(parsed[0], "http://user:pass@res.proxy-seller.com:10001")
        self.assertEqual(parsed[1], "socks5://user:pass@res.proxy-seller.com:10002")
        self.assertEqual(parsed[2], "http://user:pass@res.proxy-seller.com:10003")
        self.assertEqual(parsed[3], "http://user:pass@res.proxy-seller.com:10004")
        self.assertEqual(parsed[4], "http://res.proxy-seller.com:10005")

    def test_proxy_seller_transport_rotation(self):
        proxies = [
            "http://u1:secret1@res.proxy-seller.com:10001",
            "http://u2:secret2@res.proxy-seller.com:10002",
        ]
        transport = ProxySellerTransport(proxies)
        self.assertEqual(transport.session.proxies["http"], proxies[0])
        self.assertIn(":***@", transport.current_proxy_safe)
        self.assertNotIn("secret1", transport.current_proxy_safe)

        transport.rotate()
        self.assertEqual(transport.session.proxies["http"], proxies[1])
        self.assertIn("10002", transport.current_proxy_safe)

        transport.rotate()
        self.assertEqual(transport.session.proxies["http"], proxies[0])
        transport.close()

    def test_fetch_residential_proxies_with_resident_lists(self):
        api = MagicMock()
        api.residentLists.return_value = [{"id": 12345, "title": "List 1"}]
        api.proxyDownloadResident.return_value = "http://u:p@res.proxy-seller.com:10001\n"

        settings = Settings(proxy_seller_api_key="key123")
        res = fetch_residential_proxies(api, settings)
        self.assertEqual(res, ["http://u:p@res.proxy-seller.com:10001"])
        api.proxyDownloadResident.assert_called_once_with(id=12345)

    def test_get_proxy_seller_transport_empty_key(self):
        settings = Settings(proxy_seller_api_key="")
        self.assertIsNone(get_proxy_seller_transport(settings))

    def test_get_proxy_seller_transport_no_funds(self):
        api_mock = MagicMock()
        api_mock.__enter__.return_value = api_mock
        api_mock.balance.return_value = 0.0
        api_mock.residentPackage.return_value = {"is_active": False, "traffic_left": 0}

        with patch("judgments_core.proxy_seller.fetch_residential_proxies") as fetch_mock:
            with patch("proxy_seller_user_api.Api", return_value=api_mock):
                settings = Settings(proxy_seller_api_key="key123")
                transport = get_proxy_seller_transport(settings)
                self.assertIsNone(transport)
                fetch_mock.assert_not_called()

    def test_get_proxy_seller_transport_api_error(self):
        from proxy_seller_user_api import ApiError

        api_mock = MagicMock()
        api_mock.__enter__.side_effect = ApiError(503, {}, 200, [{"message": "Error api key", "code": 503}])

        with patch("proxy_seller_user_api.Api", return_value=api_mock):
            settings = Settings(proxy_seller_api_key="bad_key")
            transport = get_proxy_seller_transport(settings)
            self.assertIsNone(transport)

    def test_get_proxy_seller_transport_success(self):
        api_mock = MagicMock()
        api_mock.__enter__.return_value = api_mock
        api_mock.balance.return_value = 50.0

        with patch("judgments_core.proxy_seller.fetch_residential_proxies", return_value=["http://u:p@host:1000"]):
            with patch("proxy_seller_user_api.Api", return_value=api_mock):
                settings = Settings(proxy_seller_api_key="key123")
                transport = get_proxy_seller_transport(settings)
                self.assertIsNotNone(transport)
                self.assertIsInstance(transport, ProxySellerTransport)
                self.assertEqual(transport.proxies, ["http://u:p@host:1000"])

    def test_fallback_chain_rotates_and_switches(self):
        challenge = FakeResponse(403, b"challenge", {"cf-mitigated": "challenge"})
        ok = FakeResponse(200, b"ok")

        t_proxy = ProxySellerTransport(["http://u:p@host:1", "http://u:p@host:2"])
        t_direct = MagicMock()
        t_direct.name = "requests"
        t_direct.get.return_value = ok
        t_direct.headers = {}

        switches = []
        fallback = FallbackTransport(
            [("proxy_seller", lambda: t_proxy), ("requests", lambda: t_direct)],
            switch_after=2,
            on_switch=lambda *args: switches.append(args),
        )

        with patch.object(t_proxy.session, "get", return_value=challenge):
            r1 = fallback.get("https://example.test/item")
            self.assertEqual(r1.status_code, 403)
            self.assertEqual(fallback.current_name, "proxy_seller")

            r2 = fallback.get("https://example.test/item")
            self.assertEqual(r2.status_code, 200)
            self.assertEqual(fallback.current_name, "requests")
            self.assertEqual(len(switches), 1)
            self.assertEqual(switches[0][0], "proxy_seller")
            self.assertEqual(switches[0][1], "requests")

    def test_cli_make_source_prepends_proxy_seller_when_available(self):
        project = MagicMock()
        project.transports = ("requests", "curl", "playwright")
        project.source = MagicMock(return_value="source_instance")

        closer = MagicMock()

        t_proxy = ProxySellerTransport(["http://u:p@host:1"])
        settings = Settings(proxy_seller_api_key="valid_key")

        with patch("judgments_core.proxy_seller.get_proxy_seller_factory", return_value=lambda: t_proxy):
            source = _make_source(project, settings, closer)
            self.assertEqual(source, "source_instance")
            client = project.source.call_args[0][1]
            self.assertEqual(client.transport.current_name, "proxy_seller")

    def test_cli_make_source_falls_back_when_proxy_seller_fails(self):
        project = MagicMock()
        project.transports = ("requests", "curl", "playwright")
        project.source = MagicMock(return_value="source_instance")

        closer = MagicMock()

        settings = Settings(proxy_seller_api_key="invalid_key")

        with patch("judgments_core.proxy_seller.get_proxy_seller_factory", return_value=None):
            source = _make_source(project, settings, closer)
            self.assertEqual(source, "source_instance")
            client = project.source.call_args[0][1]
            self.assertEqual(client.transport.current_name, "requests")


if __name__ == "__main__":
    unittest.main()
