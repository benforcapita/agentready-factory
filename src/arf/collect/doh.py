"""Deadline-bounded HTTPS DNS JSON queries, without redirect following."""

import http.client
from ipaddress import ip_address
import json
import socket
import ssl
from threading import Timer
from time import monotonic
from urllib.parse import parse_qsl, urlencode, urlunsplit

from .transport import GuardPolicy, Refused, target


def resolve_doh(host, *, endpoint, timeout):
    """Query A and AAAA within `timeout`; one timed-out query is retried once inside the same deadline.

    Until the retry is used, each query gets half of the remaining time so a retry still fits.
    """
    parts, name = target(endpoint, GuardPolicy())
    if parts.scheme != "https" or parts.fragment:
        raise Refused("DoH endpoint must be HTTPS without a fragment")
    deadline = monotonic() + timeout
    answers = []
    retried = False
    for kind in ("A", "AAAA"):
        while True:
            remaining = deadline - monotonic()
            if remaining <= 0:
                raise TimeoutError("timeout")
            attempt_deadline = deadline if retried else monotonic() + remaining / 2
            try:
                answers.extend(_query(host, kind, parts, name, attempt_deadline))
                break
            except (OSError, http.client.HTTPException) as error:
                timed_out = isinstance(error, TimeoutError) or monotonic() >= attempt_deadline
                if retried or not timed_out or monotonic() >= deadline:
                    raise TimeoutError("timeout") if timed_out else error
                retried = True
    return answers


def _query(host, kind, parts, name, deadline):
    remaining = deadline - monotonic()
    if remaining <= 0:
        raise TimeoutError("timeout")
    connection = http.client.HTTPSConnection(name, parts.port, timeout=remaining,
                                             context=ssl.create_default_context())
    active = []

    def abort():
        if active:
            try:
                active[0].shutdown(socket.SHUT_RDWR)
            except OSError:
                pass

    timer = Timer(remaining, abort)
    timer.daemon = True
    timer.start()
    answers = []
    try:
        query = [(key, value) for key, value in parse_qsl(parts.query)
                 if key not in ("name", "type")]
        query.extend((("name", host), ("type", kind)))
        path = urlunsplit(("", "", parts.path or "/", urlencode(query), ""))
        connection.connect()
        active.append(connection.sock)
        remaining = deadline - monotonic()
        if remaining <= 0:
            raise TimeoutError("timeout")
        connection.sock.settimeout(remaining)
        connection.request("GET", path, headers={"Accept": "application/dns-json"})
        response = connection.getresponse()
        if response.status != 200:
            raise ValueError(f"DoH HTTP status {response.status}")
        body = bytearray()
        while len(body) <= 65536:
            chunk = response.read1(min(8192, 65537 - len(body)))
            if monotonic() >= deadline:
                raise TimeoutError("timeout")
            if not chunk:
                break
            body.extend(chunk)
        if len(body) > 65536:
            raise ValueError("DoH response too large")
        data = json.loads(body)
        if not isinstance(data, dict) or type(data.get("Status")) is not int or data["Status"] != 0:
            raise ValueError("malformed or unsuccessful DoH response")
        if data.get("TC", False) is not False:
            raise ValueError("truncated DoH response")
        records = data.get("Answer", [])
        if not isinstance(records, list):
            raise ValueError("malformed DoH answers")
        for record in records:
            if (not isinstance(record, dict) or type(record.get("type")) is not int
                    or not isinstance(record.get("data"), str)):
                raise ValueError("malformed DoH answer")
            if record["type"] in (1, 28):
                address = ip_address(record.get("data", ""))
                if address.version != (4 if record["type"] == 1 else 6):
                    raise ValueError("DoH address type mismatch")
                answers.append(str(address))
    finally:
        timer.cancel()
        connection.close()
    return answers
