"""HTTPS model requests pinned to validated public addresses, with bounded responses."""

import json as json_module
import socket
import ssl
import time

import requests
import urllib3

from quant_assistant.integrations.tradingagents_worker.public_https import (
    EndpointError,
    public_address,
)
from quant_assistant.integrations.tradingagents_worker.public_https import (
    endpoint as model_endpoint,
)
from quant_assistant.providers.advisor_models import AdvisorModelError


class PinnedHTTPSConnection(urllib3.connection.HTTPSConnection):
    def __init__(self, *args, candidate_addresses, **kwargs):
        super().__init__(*args, **kwargs)
        self.candidate_addresses = candidate_addresses

    def connect(self):
        original_timeout = self.timeout
        deadline = time.monotonic() + original_timeout
        error = None
        try:
            for address in self.candidate_addresses:
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise urllib3.exceptions.ConnectTimeoutError("model_connect_timeout")
                self.host = address
                self.timeout = min(3, remaining)
                try:
                    # Only TCP/TLS setup is repeated. The caller sends one POST
                    # after connect succeeds; HTTP failures never repeat a call.
                    super().connect()
                    return
                except (OSError, urllib3.exceptions.HTTPError) as caught:
                    self.close()
                    error = caught
            if isinstance(error, (TimeoutError, urllib3.exceptions.ReadTimeoutError)):
                raise urllib3.exceptions.ConnectTimeoutError("model_connect_timeout")
            if error is not None:
                raise error
            raise urllib3.exceptions.ConnectTimeoutError("model_connect_timeout")
        finally:
            self.timeout = original_timeout


def post(endpoint, *, headers, json, timeout, allow_redirects=False):
    try:
        target = model_endpoint(endpoint)
        if allow_redirects:
            raise EndpointError()
    except EndpointError:
        raise AdvisorModelError("model_configuration") from None
    try:
        addresses = socket.getaddrinfo(target.hostname, 443, type=socket.SOCK_STREAM)
        if not addresses or any(not public_address(entry[4][0]) for entry in addresses):
            raise AdvisorModelError("model_configuration")
        # TLS authenticates the requested hostname while the socket uses the
        # validated address. No second DNS lookup or environment proxy is used.
        pool = urllib3.HTTPSConnectionPool(
            addresses[0][4][0],
            port=443,
            server_hostname=target.hostname,
            assert_hostname=target.hostname,
            ssl_context=ssl.create_default_context(),
            maxsize=1,
            candidate_addresses=list(dict.fromkeys(entry[4][0] for entry in addresses)),
        )
        pool.ConnectionCls = PinnedHTTPSConnection
        response = None
        try:
            response = pool.request(
                "POST",
                target.path or "/",
                body=json_module.dumps(json, ensure_ascii=False, allow_nan=False).encode(),
                headers={**headers, "Host": target.hostname},
                timeout=urllib3.Timeout(connect=timeout[0], read=timeout[1]),
                redirect=False,
                retries=False,
                preload_content=False,
            )
            result = requests.Response()
            result.status_code = response.status
            result._content = response.read(500001)
            return result
        finally:
            if response is not None:
                response.close()
            pool.close()
    except urllib3.exceptions.ConnectTimeoutError:
        raise requests.ConnectTimeout("model_connect_timeout") from None
    except urllib3.exceptions.ReadTimeoutError:
        raise requests.ReadTimeout("model_read_timeout") from None
    except (OSError, urllib3.exceptions.HTTPError):
        raise requests.ConnectionError("model_transport_failed") from None
