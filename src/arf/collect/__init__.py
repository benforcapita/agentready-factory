"""Bounded, passive evidence collection."""

from collections import Counter
import csv
from datetime import datetime
import hashlib
from importlib.metadata import version
import http.client
import json
from pathlib import Path
import socket
from threading import Timer
from time import monotonic
from urllib.parse import urljoin, urlunsplit
from zoneinfo import ZoneInfo

from . import config
from .transport import GuardPolicy, PinnedHTTPConnection, PinnedHTTPSConnection, Refused, target


def collect(site, out, *, resolver=socket.getaddrinfo, scheme="https", port=None,
            policy=None, cap=config.MAX_REQUESTS_PER_HOST):
    """Collect a bundle; non-public test networks require explicit host allowances."""
    policy = policy or GuardPolicy()
    out = Path(out)
    if out.exists() and (not out.is_dir() or any(out.iterdir())):
        raise Refused("output directory must be empty")
    site_parts, host = target(site, policy)
    effective_port = port if port is not None else site_parts.port
    apex = host.removeprefix("www.")
    hosts = [apex, "www." + apex]
    for name in hosts:
        target(f"{scheme}://{'[' + name + ']' if ':' in name else name}", policy)
    out.mkdir(parents=True, exist_ok=True)
    started = datetime.now(ZoneInfo("Asia/Jerusalem")).isoformat()
    counts = Counter({name: 0 for name in hosts})
    errors = []

    def request(url, ua):
        parts, name = target(url, policy)
        if counts[name] >= cap:
            raise Refused(f"request cap ({cap}) reached for {name}")
        counts[name] += 1
        cls = PinnedHTTPSConnection if parts.scheme == "https" else PinnedHTTPConnection
        deadline = monotonic() + config.TIMEOUT
        connection = cls(name, parts.port, resolver=resolver, policy=policy,
                         timeout=config.TIMEOUT, deadline=deadline)
        timer = Timer(config.TIMEOUT, connection.abort)
        timer.daemon = True
        body = bytearray()
        timer.start()
        try:
            path = urlunsplit(("", "", parts.path or "/", parts.query, ""))
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
                    status, _, _, body, location = request(url, config.IDENTITIES[0][1])
                    filename = f"discovery/{host_index}-{path_index}-{hop}.body"
                    (out / filename).write_bytes(body)
                    writer.writerow((name, path, url, status, len(body), filename))
                    if status not in (301, 302, 303, 307, 308) or not location:
                        break
                    if hop == config.MAX_REDIRECTS:
                        errors.append({"url": url, "reason": "discovery redirect limit reached"})
                        break
                    url = urljoin(url, location)
                    target(url, policy)

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
        "request_count": {"total": sum(counts.values()), "per_host": dict(counts)},
        "config": {"identities": dict(config.IDENTITIES), "paths": config.PATHS,
                   "discovery_paths": config.DISCOVERY_PATHS, "timeout": config.TIMEOUT,
                   "cap_per_host": cap, "max_body_bytes": config.MAX_BODY_BYTES,
                   "max_redirects": config.MAX_REDIRECTS},
        "files": files,
    }
    (out / "evidence.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if errors:
        raise OSError(f"collection completed with {len(errors)} error(s); see errors.json")
    return manifest
