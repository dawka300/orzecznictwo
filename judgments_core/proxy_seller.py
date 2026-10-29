"""Integracja z Proxy-Seller Client API v2 (proxy-seller-user-api).

Opcjonalny krok przed łańcuchem transportów (requests -> curl -> playwright).
Jeśli podano klucz API (PROXY_SELLER_API_KEY lub <PREFIKS>_PROXY_SELLER_API_KEY),
sprawdzane są:
1. Logowanie do API (Api({'key': ..., 'timeout': 30})),
2. Stan środków na koncie (balance() > 0 lub aktywny pakiet rezydencjalny residentPackage()),
3. Pobranie listy proxy rezydencjalnych (resident / subresident).

Jeśli logowanie się nie powiedzie z jakiegokolwiek powodu (błędny klucz, brak biblioteki,
błąd sieci, IP poza allowlistą) LUB brak jest środków/aktywnego pakietu, system płynnie
przechodzi do standardowych strategii (requests -> curl -> playwright) bez przerywania
działania programu.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Callable

import requests

from judgments_core.transport import Transport

if TYPE_CHECKING:
    from judgments_core.settings import Settings

log = logging.getLogger(__name__)

try:
    from proxy_seller_user_api import ApiError
except ImportError:  # biblioteka opcjonalna (obraz www, requirements-dev), nie na hostingu

    class ApiError(Exception):
        """Zastępstwo, gdy biblioteki nie ma: nic go wtedy nie rzuca."""


# Błędy wywołań API: biblioteki, sieci i odpowiedzi o nieoczekiwanym kształcie.
API_ERRORS = (
    ApiError,
    requests.RequestException,
    OSError,
    ValueError,
    KeyError,
    TypeError,
    AttributeError,
)
DEFAULT_GATEWAY = "res.proxy-seller.com"
_GATEWAY_HOST = re.compile(r"(?<=[@/])[a-z.]*res\.proxy-seller\.com(?=:)")


class ProxySellerTransport(Transport):
    """Transport wykonujący zapytania przez proxy rezydencjalne z Proxy-Seller.

    Obsługuje rotację adresów proxy przy powtórzeniach / blokadach Cloudflare.
    """

    name = "proxy_seller"

    def __init__(self, proxies: list[str], session: requests.Session | None = None):
        super().__init__()
        self.proxies = [p for p in proxies if p]
        if not self.proxies:
            raise ValueError("Brak adresów proxy dla ProxySellerTransport")
        self.index = 0
        self.session = session or requests.Session()
        self.headers = self.session.headers
        self._apply_current_proxy()

    def _apply_current_proxy(self):
        proxy = self.proxies[self.index % len(self.proxies)]
        self.session.proxies = {"http": proxy, "https": proxy}

    def rotate(self):
        """Przełącz na kolejny adres proxy z pobranej puli."""
        if len(self.proxies) > 1:
            self.index = (self.index + 1) % len(self.proxies)
            self._apply_current_proxy()
            log.info(
                "Proxy-Seller: przełączam na kolejne proxy (%d/%d): %s",
                self.index + 1,
                len(self.proxies),
                self.current_proxy_safe,
            )

    @property
    def current_proxy(self) -> str:
        return self.proxies[self.index % len(self.proxies)]

    @property
    def current_proxy_safe(self) -> str:
        """Adres proxy z zamaskowanym hasłem do bezpiecznego logowania."""
        return re.sub(r":([^:@]+)@", ":***@", self.current_proxy)

    def get(self, url, params=None, timeout=60.0):
        return self.session.get(url, params=params, timeout=timeout)

    def close(self):
        self.session.close()


def has_positive_balance(balance_data) -> bool:
    """Sprawdza, czy w odpowiedzi balance() znajdują się dodatnie środki."""
    if balance_data is None:
        return False
    if isinstance(balance_data, (int, float)):
        return balance_data > 0
    if isinstance(balance_data, dict):
        for key in ("balance", "summ", "amount", "total", "usd", "val"):
            if key in balance_data:
                return has_positive_balance(balance_data[key])
        for val in balance_data.values():
            if has_positive_balance(val):
                return True
        return False
    if isinstance(balance_data, str):
        cleaned = re.sub(r"[^\d.-]", "", balance_data)
        try:
            return float(cleaned) > 0
        except ValueError:
            return False
    return False


def check_funds_or_package(api) -> tuple[bool, str]:
    """Sprawdza stan środków na koncie (balance) lub aktywny pakiet rezydencjalny."""
    # 1. Pakiet rezydencjalny (prepaid traffic)
    if hasattr(api, "residentPackage"):
        try:
            pkg = api.residentPackage()
            if isinstance(pkg, dict) and pkg.get("is_active"):
                left = pkg.get("traffic_left") or pkg.get("traffic_left_sub") or 0
                try:
                    left_bytes = int(left)
                    if left_bytes > 0:
                        mb = left_bytes / (1024 * 1024)
                        return True, f"aktywny pakiet rezydencjalny (pozostało {mb:.1f} MB)"
                except (ValueError, TypeError):
                    pass
        except API_ERRORS as e:
            log.debug("residentPackage() błąd: %s", e)

    # 2. Saldo gotówkowe (cash balance)
    if hasattr(api, "balance"):
        try:
            bal = api.balance()
            if has_positive_balance(bal):
                return True, f"saldo konta ({bal})"
        except API_ERRORS as e:
            log.debug("balance() błąd: %s", e)

    return False, "brak aktywnego pakietu i saldo = 0"


def parse_proxy_lines(content: str | bytes, default_protocol: str = "http") -> list[str]:
    """Rozbija pobrany plik tekstowy z Proxy-Seller na poprawne adresy URL proxy."""
    if isinstance(content, bytes):
        content = content.decode("utf-8", errors="replace")
    proxies = []
    for raw_line in content.splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if "://" in line:
            proxies.append(line)
        elif "@" in line:
            # login:password@host:port -> protocol://login:password@host:port
            proxies.append(f"{default_protocol}://{line}")
        elif line.count(":") == 3:
            # host:port:login:password -> protocol://login:password@host:port
            host, port, user, password = line.split(":")
            proxies.append(f"{default_protocol}://{user}:{password}@{host}:{port}")
        elif line.count(":") == 1:
            # host:port
            proxies.append(f"{default_protocol}://{line}")
        else:
            proxies.append(f"{default_protocol}://{line}")
    return proxies


def with_gateway(proxies: list[str], gateway: str) -> list[str]:
    """Adresy z bramą proxy rezydencjalnych z ustawień (np. us.res.proxy-seller.com)
    w miejscu tej z pobranej listy; inne hosty bez zmian."""
    return [_GATEWAY_HOST.sub(gateway, p) for p in proxies]


def fetch_residential_proxies(api, settings: Settings) -> list[str]:
    """Pobiera listę proxy rezydencjalnych (lub subresident) z API Proxy-Seller."""
    protocol = getattr(settings, "proxy_seller_protocol", "http") or "http"
    gateway = getattr(settings, "proxy_seller_gateway", DEFAULT_GATEWAY) or DEFAULT_GATEWAY
    list_id = getattr(settings, "proxy_seller_resident_list_id", "")
    package_key = getattr(settings, "proxy_seller_package_key", "")

    # 1. Sprawdź, czy podano lub istnieje resident list id
    if not list_id:
        for method_name in ("residentList", "residentLists"):
            if hasattr(api, method_name):
                try:
                    lists = getattr(api, method_name)()
                    if isinstance(lists, list) and lists:
                        list_id = lists[0].get("id")
                        break
                except API_ERRORS as e:
                    log.debug("Proxy-Seller %s() błąd: %s", method_name, e)

    # 2. Jeśli brak listy, a istnieje pakiet - utwórz domyślną listę
    if not list_id and hasattr(api, "residentListAdd"):
        try:
            new_list = api.residentListAdd("orzecznictwo-default")
            if isinstance(new_list, dict) and new_list.get("id"):
                list_id = new_list["id"]
        except API_ERRORS as e:
            log.debug("Proxy-Seller residentListAdd() błąd: %s", e)

    if list_id and hasattr(api, "proxyDownloadResident"):
        try:
            raw = api.proxyDownloadResident(id=list_id)
            proxies = with_gateway(parse_proxy_lines(raw, default_protocol=protocol), gateway)
            if proxies:
                return proxies
        except API_ERRORS as e:
            log.warning(
                "Proxy-Seller: błąd pobierania listy rezydencjalnej (id=%s): %s", list_id, e
            )

    # 3. Pakiet subresident jeśli podano package_key
    if package_key and hasattr(api, "proxyDownload"):
        try:
            raw = api.proxyDownload(
                "subresident", ext="txt", package_key=package_key, protocol=protocol
            )
            proxies = with_gateway(parse_proxy_lines(raw, default_protocol=protocol), gateway)
            if proxies:
                return proxies
        except API_ERRORS as e:
            log.warning(
                "Proxy-Seller: błąd pobierania subresident (package_key=%s): %s", package_key, e
            )

    # 4. Ogólna lista proxy (np. proxyList('resident') lub proxyList())
    if hasattr(api, "proxyList"):
        try:
            res = api.proxyList("resident")
            items = res.get("items", []) if isinstance(res, dict) else []
            proxies = []
            for item in items:
                ip = item.get("ip")
                port = item.get("port_http") or item.get("port")
                login = item.get("login")
                password = item.get("password")
                if ip and port:
                    if login and password:
                        proxies.append(f"{protocol}://{login}:{password}@{ip}:{port}")
                    else:
                        proxies.append(f"{protocol}://{ip}:{port}")
            if proxies:
                return proxies
        except API_ERRORS as e:
            log.debug("Proxy-Seller proxyList('resident') błąd: %s", e)

    return []


def get_proxy_seller_transport(settings: Settings) -> ProxySellerTransport | None:
    """Inicjalizuje ProxySellerTransport jeśli logowanie i stan środków/pakietu na to pozwalają.

    W razie jakiegokolwiek problemu (brak klucza, brak biblioteki, błędne logowanie,
    brak środków/pakietu, brak proxy) zwraca None i loguje ostrzeżenie, umożliwiając
    płynne przejście do standardowych strategii.
    """
    api_key = getattr(settings, "proxy_seller_api_key", "").strip()
    if not api_key:
        return None

    try:
        from proxy_seller_user_api import Api
    except ImportError:
        log.info(
            "Proxy-Seller: podano PROXY_SELLER_API_KEY, ale biblioteka proxy-seller-user-api "
            "nie jest zainstalowana (pip install proxy-seller-user-api). "
            "Kontynuuję ze standardowymi strategiami."
        )
        return None

    try:
        with Api({"key": api_key, "timeout": 30}) as api:
            has_funds, reason = check_funds_or_package(api)
            if not has_funds:
                log.warning(
                    "Proxy-Seller: brak środków lub aktywnego pakietu na koncie (%s). "
                    "Kontynuuję ze standardowymi strategiami.",
                    reason,
                )
                return None

            proxies = fetch_residential_proxies(api, settings)
            if not proxies:
                log.warning(
                    "Proxy-Seller: potwierdzono %s, ale nie znaleziono aktywnych proxy rezydencjalnych. "
                    "Kontynuuję ze standardowymi strategiami.",
                    reason,
                )
                return None

            transport = ProxySellerTransport(proxies)
            log.info(
                "Proxy-Seller: zalogowano pomyślnie (%s, pobrano %d proxy) - "
                "aktywuję jako pierwszy transport przed strategiami.",
                reason,
                len(proxies),
            )
            return transport

    except ApiError as error:
        if getattr(error, "isAccessError", lambda: False)():
            reasons = [
                item.get("message")
                for item in getattr(error, "errors", [])
                if isinstance(item, dict)
            ]
            log.warning(
                "Proxy-Seller: błąd dostępu do API (%s). Kontynuuję ze standardowymi strategiami.",
                ", ".join(reasons) or error,
            )
        else:
            log.warning(
                "Proxy-Seller: błąd API (kod %s, błędy: %s). Kontynuuję ze standardowymi strategiami.",
                getattr(error, "code", None),
                getattr(error, "errors", error),
            )
        return None
    except API_ERRORS as error:
        log.warning(
            "Proxy-Seller: nieoczekiwany błąd podczas łączenia z API (%s). Kontynuuję ze standardowymi strategiami.",
            error,
        )
        return None


def get_proxy_seller_factory(settings: Settings) -> Callable[[], ProxySellerTransport] | None:
    """Zwraca funkcję fabryczną tworzącą transport, jeśli Proxy-Seller jest gotowy."""
    transport = get_proxy_seller_transport(settings)
    if transport is None:
        return None
    return lambda: transport


def make_proxy_seller_from_env() -> ProxySellerTransport:
    """Używane przy wywołaniu 'proxy_seller' bezpośrednio w nazwach transportów."""
    from judgments_core.settings import Settings

    settings = Settings.from_env("etpc")
    transport = get_proxy_seller_transport(settings)
    if transport is None:
        raise RuntimeError(
            "Proxy-Seller jest niedostępny (sprawdź klucz API, stan środków lub instalację biblioteki)."
        )
    return transport
