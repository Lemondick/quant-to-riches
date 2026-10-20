"""Standard-library-only public HTTPS boundary for the isolated research child."""

import http.client
import ipaddress
import re
import socket
import time
from urllib.parse import urlsplit


class EndpointError(ValueError):
    def __init__(self):
        super().__init__("model_not_configured")


def endpoint(value):
    try:
        if (
            not isinstance(value, str)
            or len(value) > 500
            or any(ord(char) <= 32 or ord(char) == 127 for char in value)
            or any(char in value for char in ("?", "#", "%", "\\"))
        ):
            raise EndpointError()
        target = urlsplit(value)
        if (
            target.scheme != "https"
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.port not in {None, 443}
            or target.query
            or target.fragment
        ):
            raise EndpointError()
        path = target.path.rstrip("/")
        if "//" in target.path or any(
            part in {".", ".."} or re.fullmatch(r"[A-Za-z0-9._~-]+", part) is None
            for part in path.split("/")[1:]
        ):
            raise EndpointError()
        target.hostname.encode("idna")
        return target
    except (ValueError, UnicodeError):
        raise EndpointError() from None


def public_address(value):
    address = ipaddress.ip_address(value)
    # IANA's retired relay block (including the non-global 6a44 address) is
    # still classified global by some supported Python maintenance versions.
    if isinstance(address, ipaddress.IPv4Address) and address in ipaddress.IPv4Network(
        "192.88.99.0/24"
    ):
        return False
    if isinstance(address, ipaddress.IPv6Address):
        if address.ipv4_mapped:
            return public_address(str(address.ipv4_mapped))
        if (
            address not in ipaddress.IPv6Network("2000::/3")
            or address.sixtofour is not None
            or address.teredo is not None
        ):
            return False
    return address.is_global and not address.is_multicast and not address.is_reserved


class PinnedHTTPSConnection(http.client.HTTPSConnection):
    def connect(self):
        if self.port != 443 or self._tunnel_host:
            raise EndpointError()
        deadline = time.monotonic() + self.timeout
        records = socket.getaddrinfo(
            self.host, 443, type=socket.SOCK_STREAM, proto=socket.IPPROTO_TCP
        )
        candidates = []
        # Check the entire answer before opening even the first public socket.
        try:
            for family, _, _, _, sockaddr in records:
                value = sockaddr[0]
                address = ipaddress.ip_address(value)
                if (
                    family not in {socket.AF_INET, socket.AF_INET6}
                    or (family == socket.AF_INET) != (address.version == 4)
                    or "%" in value
                    or not public_address(value)
                ):
                    raise EndpointError()
                candidate = (family, str(address))
                if candidate not in candidates:
                    candidates.append(candidate)
        except (ValueError, TypeError, IndexError):
            raise EndpointError() from None
        if not candidates:
            raise EndpointError()
        error = None
        for family, value in candidates:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("model_connect_timeout")
            raw = socket.socket(family, socket.SOCK_STREAM, socket.IPPROTO_TCP)
            try:
                raw.settimeout(remaining)
                target = (value, 443, 0, 0) if family == socket.AF_INET6 else (value, 443)
                # Numeric sockaddr directly: never resolve the hostname again.
                raw.connect(target)
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError("model_connect_timeout")
                raw.settimeout(remaining)
                self.sock = self._context.wrap_socket(raw, server_hostname=self.host)
                return
            except OSError as caught:
                raw.close()
                error = caught
        if isinstance(error, TimeoutError):
            raise TimeoutError("model_connect_timeout") from None
        raise OSError("model_transport_failed") from None
