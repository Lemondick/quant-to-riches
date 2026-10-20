"""Offline model transport tests: synthetic key, DNS, TCP, TLS and HTTP only."""

import http.client
import json
import os
import socket
import ssl
import time
import unittest
from contextlib import contextmanager
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from quant_assistant.integrations.tradingagents_worker import main as adapter


def answer(address):
    family = socket.AF_INET6 if ":" in address else socket.AF_INET
    target = (address, 443, 0, 0) if family == socket.AF_INET6 else (address, 443)
    return (family, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", target)


@contextmanager
def transport(addresses=None):
    raw = MagicMock()
    tls = MagicMock(check_hostname=True, verify_mode=ssl.CERT_REQUIRED)
    tls.wrap_socket.side_effect = lambda connection, **kwargs: connection
    response = MagicMock(status=200)
    response.read.return_value = json.dumps(
        {"choices": [{"finish_reason": "stop", "message": {"content": "{}"}}]}
    ).encode()
    with (
        patch.dict(os.environ, QUANT_AGENT_KEY_FILE="synthetic-unused"),
        patch.object(Path, "read_text", return_value="synthetic-offline-value"),
        patch("socket.getaddrinfo", return_value=addresses or [answer("8.8.8.8")]) as dns,
        patch("socket.socket", return_value=raw) as create,
        patch("ssl.create_default_context", return_value=tls),
        patch.object(http.client.HTTPSConnection, "getresponse", return_value=response) as read,
    ):
        yield SimpleNamespace(
            raw=raw, tls=tls, dns=dns, create=create, read=read, response=response
        )


def call(endpoint="https://models.example.org/v1", *, deadline=None):
    return adapter.call_model(
        {"endpoint": endpoint, "model": "synthetic"},
        "bull",
        {"symbol": "600000.SH", "data_date": "2026-10-20"},
        {},
        deadline if deadline is not None else time.monotonic() + 10,
        [1],
    )


class ModelNetworkBoundaryTests(unittest.TestCase):
    def test_every_nonpublic_answer_is_rejected_before_any_socket_or_post(self):
        for address in (
            "127.0.0.1",
            "10.0.0.1",
            "172.16.0.1",
            "192.168.1.1",
            "169.254.169.254",
            "0.0.0.0",
            "100.64.0.1",
            "192.0.2.1",
            "198.18.0.1",
            "224.0.0.1",
            "240.0.0.1",
            "::1",
            "::",
            "fe80::1",
            "fc00::1",
            "ff02::1",
            "2001:db8::1",
            "::ffff:127.0.0.1",
            "64:ff9b::a00:1",
            "2002:0a00:0001::1",
            "192.88.99.1",
        ):
            with self.subTest(address=address), transport([answer(address)]) as io:
                with self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"):
                    call()
                io.create.assert_not_called()
                io.read.assert_not_called()

    def test_mixed_public_private_dns_answer_fails_as_a_whole(self):
        with transport([answer("8.8.8.8"), answer("127.0.0.1")]) as io:
            with self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"):
                call()
            io.create.assert_not_called()

    def test_discovery_public_then_runtime_private_rebinding_is_denied(self):
        with transport() as io:
            io.dns.side_effect = [[answer("8.8.8.8")], [answer("10.0.0.1")]]
            socket.getaddrinfo("models.example.org", 443)  # synthetic earlier discovery
            with self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"):
                call()
            self.assertEqual(io.dns.call_count, 2)
            io.create.assert_not_called()

    def test_unsafe_urls_are_sanitized_before_dns(self):
        for endpoint in (
            "http://models.example.org/v1",
            "https://user:example@models.example.org/v1",
            "https://@models.example.org/v1",
            "https://models.example.org:444/v1",
            "https://models.example.org:invalid/v1",
            "https://models.example.org/v1?key=example",
            "https://models.example.org/v1#example",
            "https://models.example.org/v1?",
            "https://models.example.org/v1#",
            "https://models.example.org/a/../v1",
            "https://models.example.org/%2fprivate",
            "https://models.example.org//v1",
            "https://models.example.org/v1\n",
            "https://models.example.org/v1\\other",
            "https://[invalid/v1",
        ):
            with self.subTest(endpoint=endpoint), transport() as io:
                with self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"):
                    call(endpoint)
                io.dns.assert_not_called()
                io.create.assert_not_called()

    def test_public_ipv4_and_ipv6_pin_numeric_socket_and_original_tls_host(self):
        for address in ("8.8.8.8", "2606:4700:4700::1111", "::ffff:8.8.8.8"):
            with self.subTest(address=address), transport([answer(address)]) as io:
                io.dns.side_effect = [[answer(address)], [answer("10.0.0.1")]]
                self.assertEqual(call(), ({}, None))
                self.assertEqual(io.dns.call_count, 1)
                self.assertEqual(io.raw.connect.call_args.args[0][0], address)
                self.assertEqual(
                    io.tls.wrap_socket.call_args.kwargs["server_hostname"], "models.example.org"
                )
                headers = io.raw.sendall.call_args_list[0].args[0]
                self.assertIn(b"Host: models.example.org\r\n", headers)
                self.assertTrue(io.tls.check_hostname)
                self.assertEqual(io.tls.verify_mode, ssl.CERT_REQUIRED)

    def test_tls_failure_can_try_another_validated_address_before_post(self):
        with transport([answer("2606:4700:4700::1111"), answer("8.8.8.8")]) as io:
            second = MagicMock()
            io.create.side_effect = [io.raw, second]
            io.tls.wrap_socket.side_effect = [ssl.SSLError("synthetic TLS failure"), second]
            self.assertEqual(call(), ({}, None))
            io.raw.close.assert_called()
            io.raw.sendall.assert_not_called()
            self.assertEqual(second.connect.call_args.args[0], ("8.8.8.8", 443))
            self.assertEqual(io.dns.call_count, 1)

    def test_one_total_connect_budget_blocks_slow_fallback(self):
        with transport([answer("8.8.8.8"), answer("1.1.1.1")]) as io:
            now = [0.0]

            def fail(target):
                now[0] = 6.0
                raise TimeoutError("synthetic slow setup")

            io.raw.connect.side_effect = fail
            with (
                patch("time.monotonic", side_effect=lambda: now[0]),
                self.assertRaisesRegex(adapter.WorkerError, "^timed_out$"),
            ):
                call(deadline=10)
            self.assertEqual(io.create.call_count, 1)
            io.raw.close.assert_called()
            io.raw.sendall.assert_not_called()

    def test_read_timeout_never_replays_post_and_closes_socket(self):
        with transport() as io:
            io.read.side_effect = TimeoutError("synthetic read timeout")
            with self.assertRaisesRegex(adapter.WorkerError, "^timed_out$"):
                call()
            posts = [
                args.args[0]
                for args in io.raw.sendall.call_args_list
                if args.args[0].startswith(b"POST ")
            ]
            self.assertEqual(len(posts), 1)
            self.assertEqual(io.dns.call_count, 1)
            io.raw.close.assert_called()

    def test_dns_time_consumes_setup_budget_before_any_socket(self):
        with transport() as io:
            now = [0.0]

            def resolve(*args, **kwargs):
                now[0] = 6.0
                return [answer("8.8.8.8")]

            io.dns.side_effect = resolve
            with (
                patch("time.monotonic", side_effect=lambda: now[0]),
                self.assertRaisesRegex(adapter.WorkerError, "^timed_out$"),
            ):
                call(deadline=10)
            io.create.assert_not_called()

    def test_supported_simple_api_prefixes_and_full_chat_path_are_retained(self):
        for path in ("", "/", "/v1/", "/api/v1", "/vendor-a/v2/chat/completions"):
            with self.subTest(path=path), transport() as io:
                self.assertEqual(call("https://models.example.org" + path), ({}, None))
                headers = io.raw.sendall.call_args_list[0].args[0]
                expected = path.rstrip("/")
                if not expected.endswith("/chat/completions"):
                    expected += "/chat/completions"
                self.assertTrue(headers.startswith(("POST " + expected + " HTTP/1.1").encode()))

    def test_missing_key_never_resolves_or_connects(self):
        with transport() as io, patch.object(Path, "read_text", return_value=""):
            with self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"):
                call()
            io.dns.assert_not_called()
            io.create.assert_not_called()

    def test_definite_rate_limit_retry_revalidates_dns_without_a_second_private_dial(self):
        with transport() as io, patch("time.sleep"):
            io.read.return_value.status = 429
            io.dns.side_effect = [[answer("8.8.8.8")], [answer("10.0.0.1")]]
            with self.assertRaisesRegex(adapter.WorkerError, "^model_not_configured$"):
                call()
            self.assertEqual(io.dns.call_count, 2)
            self.assertEqual(io.create.call_count, 1)
            posts = [
                args.args[0]
                for args in io.raw.sendall.call_args_list
                if args.args[0].startswith(b"POST ")
            ]
            self.assertEqual(len(posts), 1)

    def test_redirect_is_not_followed_or_replayed(self):
        with transport() as io:
            io.read.return_value.status = 302
            with self.assertRaisesRegex(adapter.WorkerError, "^model_error$"):
                call()
            self.assertEqual(io.dns.call_count, 1)
            self.assertEqual(io.create.call_count, 1)

    def test_provider_cannot_echo_the_key_into_worker_output(self):
        with transport() as io:
            io.response.read.return_value = json.dumps(
                {
                    "choices": [
                        {
                            "finish_reason": "stop",
                            "message": {"content": json.dumps({"text": "synthetic-offline-value"})},
                        }
                    ]
                }
            ).encode()
            with self.assertRaisesRegex(adapter.WorkerError, "^invalid_output$"):
                call()

    def test_direct_tls_context_default_certificate_verification(self):
        # Verify the real default independently from the mocked TCP/TLS fixtures.
        context = ssl.create_default_context()
        self.assertTrue(context.check_hostname)
        self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)


if __name__ == "__main__":
    unittest.main()
