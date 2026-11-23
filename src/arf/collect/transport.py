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
    ipv4_literal, is_fake_ip, is_public as validation_is_public,
)


SIX_TO_FOUR = ip_network("2002::/16")


def is_public(address):
    return not (address.version == 6 and address in SIX_TO_FOUR) and validation_is_public(address)


class BudgetExhausted(Exception):
    """No further site connection attempts are permitted."""


class Refused(ValueError):
    """An unsafe target or unverifiable egress."""


@dataclass
class GuardPolicy:
    allowed_networks: dict = field(default_factory=dict)

    def allows(self, host, address):
        if address.version == 6 and address in SIX_TO_FOUR:
            return False
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
    def __init__(self, host, port, *, resolver, policy, timeout, deadline=None,
                 fake_ip_policy="refuse", doh_endpoint=None, doh_resolver=None,
                 doh_answers=None, before_connect=None):
        super().__init__(host, port, timeout=timeout)
        self.resolver = resolver
        self.policy = policy
        self.deadline = deadline
        self.expired = Event()
        self.active_socket = None
        self.fake_ip_policy = fake_ip_policy
        self.doh_endpoint = doh_endpoint
        self.doh_resolver = doh_resolver
        self.doh_answers = doh_answers if doh_answers is not None else {}
        self.before_connect = before_connect

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

    def bounded(self, callback):
        result = Queue(maxsize=1)

        def lookup():
            try:
                result.put((callback(), None))
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

    def resolve(self):
        lookup = lambda: self.resolver(self.host, self.port, type=socket.SOCK_STREAM)
        return lookup() if self.deadline is None else self.bounded(lookup)

    def verify_doh(self):
        try:
            answers = self.bounded(lambda: self.doh_resolver(
                self.host, endpoint=self.doh_endpoint, timeout=self.remaining()))
            if not isinstance(answers, (list, tuple)) or not answers:
                raise ValueError("no A/AAAA answers")
            if any(not isinstance(answer, str) for answer in answers):
                raise ValueError("malformed A/AAAA answer")
            addresses = [ip_address(answer) for answer in answers]
            checked = self.doh_answers.setdefault(self.host, [])
            checked[:] = sorted(set(checked) | {str(address) for address in addresses})
            if any(not is_public(address) or is_fake_ip(address) for address in addresses):
                raise ValueError("DoH answers must all be public")
            self.remaining()
        except Exception as error:
            raise Refused(f"{self.host}: DoH verification failed: {error}") from None

    def connect(self):
        records = self.resolve()
        if not records:
            raise Refused(f"could not resolve {self.host}")
        addresses = [ip_address(record[4][0]) for record in records]
        fake = [is_fake_ip(address) for address in addresses]
        if any(fake):
            if not all(fake):
                raise Refused(f"{self.host}: mixed fake-IP and real DNS answers")
            if self.fake_ip_policy != "doh_verify":
                raise Refused(f"{self.host}: fake-IP answer; cannot verify real egress; "
                              "set ARF_FAKE_IP_POLICY=doh_verify (factory runtime default) to re-resolve over DoH")
            self.verify_doh()
        else:
            if any(not self.policy.allows(self.host, address) for address in addresses):
                raise Refused(f"{self.host}: DNS must resolve only to public addresses")
        last_error = None
        for family, socktype, proto, _, sockaddr in records:
            self.remaining()
            if self.before_connect is not None:
                try:
                    self.before_connect()
                except BudgetExhausted:
                    if last_error is not None:
                        raise last_error
                    raise
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
