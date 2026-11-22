"""Classify targets and pin connections to vetted DNS answers."""

from dataclasses import dataclass, field
import http.client
from ipaddress import ip_address, ip_network
import re
from queue import Empty, Queue
import socket
import ssl
from threading import Event, Thread
from time import monotonic
from urllib.parse import urlsplit

from ..validation import (
    InvalidRequest, SPECIAL_USE_SUFFIXES, canonical_host, folded_host,
    ipv4_literal, is_fake_ip, is_public,
)


class Refused(ValueError):
    """An unsafe target or exhausted request budget."""


@dataclass
class GuardPolicy:
    allowed_networks: dict = field(default_factory=dict)

    def allows(self, host, address):
        return is_public(address) or any(
            address in ip_network(network)
            for network in self.allowed_networks.get(host, ())
        )


def target(url, policy):
    try:
        if "\\" in url or any(c.isspace() or ord(c) < 32 for c in url):
            raise ValueError("invalid URL")
        parts = urlsplit(url)
        raw = parts.hostname
        if parts.scheme not in ("http", "https") or not raw or parts.username is not None:
            raise ValueError("HTTP(S) targets without credentials required")
        if "%" in raw or parts.port == 0:
            raise ValueError("invalid host or port")
        if parts.netloc.startswith("[") and not re.fullmatch(r"\[[0-9a-fA-F:.]+\](?::[0-9]*)?", parts.netloc):
            raise ValueError("invalid IPv6 authority")
        host = canonical_host(raw, "invalid host")
        address = ipv4_literal(host, "invalid numeric host")
        folded = folded_host(raw)
        alternate = ipv4_literal(folded, "invalid numeric host") if folded else None
        for literal in (address, alternate):
            if literal is not None and (is_fake_ip(literal) or not policy.allows(host, literal)):
                raise Refused(f"refused non-public literal: {host}")
        if address is None:
            if (len(host) > 253 or "." not in host
                    or host.endswith(tuple("." + s for s in SPECIAL_USE_SUFFIXES))
                    or any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                           for label in host.split("."))):
                raise Refused(f"refused non-public or invalid host: {host}")
        return parts, host
    except (ValueError, InvalidRequest) as error:
        raise Refused(str(error)) from None


class PinnedHTTPConnection(http.client.HTTPConnection):
    def __init__(self, host, port, *, resolver, policy, timeout, deadline=None):
        super().__init__(host, port, timeout=timeout)
        self.resolver = resolver
        self.policy = policy
        self.deadline = deadline
        self.expired = Event()
        self.active_socket = None

    def remaining(self):
        remaining = self.timeout if self.deadline is None else self.deadline - monotonic()
        if self.expired.is_set() or remaining <= 0:
            raise TimeoutError("timeout")
        return remaining

    def abort(self):
        self.expired.set()
        sock = self.active_socket
        if sock is not None:
            try:
                sock.shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    def resolve(self):
        if self.deadline is None:
            return self.resolver(self.host, self.port, type=socket.SOCK_STREAM)
        result = Queue(maxsize=1)

        def lookup():
            try:
                result.put((self.resolver(self.host, self.port, type=socket.SOCK_STREAM), None))
            except Exception as error:
                result.put((None, error))

        Thread(target=lookup, daemon=True).start()
        try:
            records, error = result.get(timeout=self.remaining())
        except Empty:
            raise TimeoutError("timeout") from None
        self.remaining()
        if error is not None:
            raise error
        return records

    def connect(self):
        records = self.resolve()
        if not records:
            raise Refused(f"could not resolve {self.host}")
        for record in records:
            address = ip_address(record[4][0])
            if is_fake_ip(address):
                raise Refused(f"{self.host}: fake-IP answer; cannot verify real egress")
            if not self.policy.allows(self.host, address):
                raise Refused(f"{self.host}: DNS must resolve only to public addresses")
        last_error = None
        for family, socktype, proto, _, sockaddr in records:
            sock = socket.socket(family, socktype, proto)
            try:
                self.sock = sock
                self.active_socket = sock
                sock.settimeout(self.remaining())
                sock.connect(sockaddr)
                self.remaining()
                return
            except OSError as error:
                sock.close()
                last_error = error
        raise last_error


class PinnedHTTPSConnection(PinnedHTTPConnection):
    default_port = 443

    def connect(self):
        super().connect()
        try:
            self.sock.settimeout(self.remaining())
            self.sock = ssl.create_default_context().wrap_socket(self.sock, server_hostname=self.host)
            self.active_socket = self.sock
            self.remaining()
        except Exception:
            self.close()
            raise
