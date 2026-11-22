"""Bounded, passive evidence collection."""

from collections import Counter
import csv
from datetime import datetime
import hashlib
from importlib.metadata import version
import http.client
import json
import os
from pathlib import Path
import socket
from threading import Timer
from time import monotonic
from urllib.parse import urljoin, urlunsplit
from zoneinfo import ZoneInfo

from . import config
from .doh import resolve_doh
from .transport import GuardPolicy, PinnedHTTPConnection, PinnedHTTPSConnection, Refused, target
from ..validation import ipv4_literal


def collect(site, out, *, resolver=socket.getaddrinfo, scheme="https", port=None,
            policy=None, max_requests=None,
            fake_ip_policy=None, doh_endpoint=None,
            doh_resolver=resolve_doh):
    """Collect a bundle; non-public test networks require explicit host allowances."""
    policy = policy or GuardPolicy()
    if max_requests is None:
        try:
            max_requests = int(os.environ.get("ARF_MAX_REQUESTS", config.MAX_REQUESTS))
        except ValueError:
            raise ValueError("ARF_MAX_REQUESTS must be a non-negative integer") from None
    if fake_ip_policy is None:
        fake_ip_policy = os.environ.get("ARF_FAKE_IP_POLICY", config.FAKE_IP_POLICY)
    if doh_endpoint is None:
        doh_endpoint = os.environ.get("ARF_DOH_ENDPOINT", config.DOH_ENDPOINT)
    if type(max_requests) is not int or max_requests < 0:
        raise ValueError("max_requests must be a non-negative integer")
    if fake_ip_policy not in ("refuse", "doh_verify"):
        raise ValueError("fake_ip_policy must be refuse or doh_verify")
    endpoint, _ = target(doh_endpoint, GuardPolicy())
    if endpoint.scheme != "https" or endpoint.fragment:
        raise ValueError("DoH endpoint must be HTTPS without a fragment")
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused("output directory must be empty")
    site_parts, host = target(site, policy)
    effective_port = port if port is not None else site_parts.port
    apex = host.removeprefix("www.")
    literal = ipv4_literal(host, "invalid numeric host")
    hosts = [str(literal)] if literal is not None else [apex, "www." + apex]
    for name in hosts:
        target(f"{scheme}://{'[' + name + ']' if ':' in name else name}", policy)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat()
    counts = Counter({name: 0 for name in hosts})
    errors = []
    doh_answers = {}

    def request(url, ua):
        if sum(counts.values()) >= max_requests:
            return "skipped_budget", "", "", b"", None
        parts, name = target(url, policy)
        cls = PinnedHTTPSConnection if parts.scheme == "https" else PinnedHTTPConnection
        deadline = monotonic() + config.TIMEOUT
        connection = cls(name, parts.port, resolver=resolver, policy=policy,
                         timeout=config.TIMEOUT, deadline=deadline,
                         fake_ip_policy=fake_ip_policy, doh_endpoint=doh_endpoint,
                         doh_resolver=doh_resolver, doh_answers=doh_answers)
        timer = Timer(config.TIMEOUT, connection.abort)
        timer.daemon = True
        body = bytearray()
        timer.start()
        try:
            path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
            connection.connect()
            connection.remaining()
            counts[name] += 1
            connection.request("GET", path, headers={"User-Agent": ua, "Accept": "*/*"})
            response = connection.getresponse()
            while len(body) < config.MAX_BODY_BYTES:
                connection.remaining()
                chunk = response.read1(min(65536, config.MAX_BODY_BYTES - len(body)))
                connection.remaining()
                if not chunk:
                    break
                body.extend(chunk)
            connection.remaining()
            return response.status, response.getheader("Server", ""), response.getheader("CF-Ray", ""), bytes(body), response.getheader("Location")
        except (OSError, http.client.HTTPException) as error:
            reason = "timeout" if isinstance(error, TimeoutError) or connection.expired.is_set() or monotonic() >= deadline else str(error)[:200]
            errors.append({"url": url, "reason": reason})
            return "error", "", "", bytes(body), None
        finally:
            timer.cancel()
            connection.close()

    def initial_url(name, path):
        authority = f"[{name}]" if ":" in name else name
        if effective_port is not None:
            authority += f":{effective_port}"
        return f"{scheme}://{authority}{path}"

    with (out / "probes.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("domain", "path", "agent", "status", "server", "cf_ray", "size"))
        for name in hosts:
            for path in config.PATHS:
                for agent, ua in config.IDENTITIES:
                    status, server, ray, body, _ = request(initial_url(name, path), ua)
                    writer.writerow((name, path, agent, status, server, ray, len(body)))

    (out / "discovery").mkdir()
    with (out / "discovery.csv").open("w", newline="", encoding="utf-8") as stream:
        writer = csv.writer(stream, lineterminator="\n")
        writer.writerow(("domain", "path", "url", "status", "size", "body"))
        for host_index, name in enumerate(hosts):
            for path_index, path in enumerate(config.DISCOVERY_PATHS):
                url = initial_url(name, path)
                for hop in range(config.MAX_REDIRECTS + 1):
                    try:
                        status, _, _, body, location = request(url, config.IDENTITIES[0][1])
                    except Refused as error:
                        errors.append({"url": url, "domain": name, "path": path,
                                       "status": "refused", "reason": str(error)})
                        writer.writerow((name, path, url, "refused", 0, ""))
                        break
                    filename = f"discovery/{host_index}-{path_index}-{hop}.body"
                    (out / filename).write_bytes(body)
                    writer.writerow((name, path, url, status, len(body), filename))
                    if status not in (301, 302, 303, 307, 308) or not location:
                        break
                    if hop == config.MAX_REDIRECTS:
                        errors.append({"url": url, "reason": "discovery redirect limit reached"})
                        break
                    try:
                        url = urljoin(url, location)
                    except ValueError as error:
                        errors.append({"url": url, "domain": name, "path": path,
                                       "status": "error", "reason": f"invalid redirect: {error}"})
                        writer.writerow((name, path, location, "error", 0, ""))
                        break

    (out / "errors.json").write_text(json.dumps(errors, indent=2) + "\n", encoding="utf-8")
    files = []
    for path in sorted(out.rglob("*"), key=lambda path: path.relative_to(out).as_posix()):
        if path.is_file():
            data = path.read_bytes()
            files.append({"path": path.relative_to(out).as_posix(), "bytes": len(data),
                          "sha256": hashlib.sha256(data).hexdigest()})
    manifest = {
        "tool": {"name": "arf", "version": version("agentready-factory")},
        "site": site, "hosts": hosts, "started_at": started,
        "finished_at": datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat(),
        "requests_made": sum(counts.values()), "max_requests": max_requests,
        "request_count": {"total": sum(counts.values()), "per_host": dict(counts),
                          "requests_made": sum(counts.values()), "max_requests": max_requests},
        "fake_ip_policy": fake_ip_policy, "doh_answers": doh_answers,
        "config": {"identities": dict(config.IDENTITIES), "paths": config.PATHS,
                   "discovery_paths": config.DISCOVERY_PATHS, "timeout": config.TIMEOUT,
                   "max_requests": max_requests, "fake_ip_policy": fake_ip_policy,
                   "doh_endpoint": doh_endpoint, "max_body_bytes": config.MAX_BODY_BYTES,
                   "max_redirects": config.MAX_REDIRECTS},
        "files": files,
    }
    (out / "evidence.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise OSError(f"collection completed with {len(errors)} error(s); see errors.json")
    return manifest
