"""Load and validate a request without fetching its site."""

from ipaddress import IPv4Address, IPv4Network, IPv6Address, IPv6Network, ip_address
from pathlib import Path
import re
import socket
import unicodedata
from urllib.parse import urlsplit

import yaml
from pydantic import ValidationError

from .request import PHASES, Phase, Request


class InvalidRequest(ValueError):
    """A user-facing request error."""


class RequestLoader(yaml.SafeLoader):
    # The schema validates ISO dates and reports failures against the right field.
    yaml_implicit_resolvers = {
        key: [entry for entry in entries if entry[0] != "tag:yaml.org,2002:timestamp"]
        for key, entries in yaml.SafeLoader.yaml_implicit_resolvers.items()
    }

    def construct_mapping(self, node, deep=False):
        mapping = super().construct_mapping(node, deep=deep)
        if len(mapping) != len(node.value):
            line = node.start_mark.line + 1
            raise InvalidRequest(f"invalid YAML at line {line}: duplicate mapping key")
        return mapping


# Proxied runtimes (Clash, sing-box, Surge) answer every lookup from RFC 2544
# space, so these answers carry no information about the real host.
FAKE_IP_NETWORK = IPv4Network("198.18.0.0/15")

HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)
# Resolvers and URL parsers read a host whose last label is numeric as an IPv4
# literal in any inet_aton form (127.1, 0x7f.1, 0177.0.0.1, 2130706433).
NUMERIC_LABEL = re.compile(r"0x[0-9a-f]*|[0-9]+", re.IGNORECASE)
# URL parsers fold these to "." and drop invisible characters before resolving.
IDEOGRAPHIC_DOTS = re.compile("[\u3002\uff0e\uff61]")
SIXTOFOUR_NETWORK = IPv6Network("2002::/16")
# Teredo relays traffic through a third-party server, so reject the whole prefix.
TEREDO_NETWORK = IPv6Network("2001::/32")
# IANA IETF Protocol Assignments. Python patch releases disagree on its anycast
# and infrastructure sub-prefixes (2001:1::1, 2001:3::/32, 2001:4:112::/48,
# 2001:20::/28, 2001:30::/28), and none of them hosts a website, so reject all of it.
IETF_PROTOCOL_NETWORK = IPv6Network("2001::/23")
NAT64_NETWORKS = (IPv6Network("64:ff9b::/96"), IPv6Network("64:ff9b:1::/48"))
SIIT_NETWORK = IPv6Network("::ffff:0:0:0/96")
SPECIAL_USE_SUFFIXES = ("localhost", "local", "internal", "home.arpa", "test", "invalid", "onion", "alt")


def is_public(address):
    if isinstance(address, IPv6Address):
        # Python patch releases disagree on whether these prefixes are global.
        if any(
            address in network
            for network in (SIXTOFOUR_NETWORK, TEREDO_NETWORK, IETF_PROTOCOL_NETWORK)
        ):
            return False
        if not address.is_global or address.is_site_local or address.is_multicast:
            return False
        embedded = address.ipv4_mapped
        if embedded is None and (
            int(address) >> 32 == 0
            or address in SIIT_NETWORK
            or any(address in net for net in NAT64_NETWORKS)
        ):
            embedded = IPv4Address(int(address) & 0xFFFFFFFF)
        if embedded is not None:
            address = embedded
    return address.is_global and not address.is_multicast


def is_fake_ip(address):
    if isinstance(address, IPv6Address):
        address = address.ipv4_mapped
    return address is not None and address in FAKE_IP_NETWORK


def canonical_host(hostname, invalid_url):
    """The ASCII host a client resolves, after IDNA folding (fullwidth digits,
    ideographic dots and invisible characters all fold away)."""
    try:
        host = hostname.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise InvalidRequest(invalid_url) from None
    return host.removesuffix(".")


def folded_host(hostname):
    """An approximation of the UTS 46 mapping used by WHATWG URL parsers, as a
    second opinion on non-ASCII hosts in case it folds differently to IDNA 2003."""
    if hostname.isascii():
        return None
    host = unicodedata.normalize("NFKC", hostname).casefold()
    host = "".join(
        char for char in host
        if unicodedata.category(char) not in ("Cf", "Mn") and char != "\u034f"
    )
    return IDEOGRAPHIC_DOTS.sub(".", host).removesuffix(".")


def ipv4_literal(host, invalid_url):
    """Parse a host that a URL parser would treat as an IP literal: any host
    whose last label is a number, in every inet_aton form."""
    try:
        return ip_address(host)
    except ValueError:
        pass
    if not NUMERIC_LABEL.fullmatch(host.rsplit(".", 1)[-1]):
        return None
    try:
        return IPv4Address(socket.inet_aton(host))
    except (OSError, UnicodeError, ValueError):
        raise InvalidRequest(invalid_url) from None


def validate_site(site: str) -> list[str]:
    invalid_url = "site: must be an HTTPS URL with a valid host and no credentials"
    public_host_required = "site: host must be public"
    if "\\" in site or any(char.isspace() or ord(char) < 32 for char in site):
        raise InvalidRequest(invalid_url)
    try:
        url = urlsplit(site)
        hostname = url.hostname
        port = url.port
    except ValueError:
        raise InvalidRequest(invalid_url) from None
    if url.scheme != "https" or not hostname or url.username is not None or port == 0:
        raise InvalidRequest(invalid_url)
    if url.netloc.startswith("[") and not re.fullmatch(
        r"\[[0-9a-fA-F:.]+\](?::[0-9]*)?", url.netloc
    ):
        raise InvalidRequest(invalid_url)

    if "%" in hostname:
        raise InvalidRequest(invalid_url)
    try:
        address = ip_address(hostname.removesuffix("."))
    except ValueError:
        host = canonical_host(hostname, invalid_url)
        folded = folded_host(hostname)
        address = ipv4_literal(host, invalid_url)
        if address is None and folded:
            address = ipv4_literal(folded, invalid_url)
    if address is not None:
        if not is_public(address):
            raise InvalidRequest(public_host_required)
        return []

    if len(host) > 253 or any(not HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise InvalidRequest(invalid_url)
    if "." not in host or host.endswith(tuple("." + suffix for suffix in SPECIAL_USE_SUFFIXES)):
        raise InvalidRequest(public_host_required)
    try:
        records = socket.getaddrinfo(f"{host}.", None, type=socket.SOCK_STREAM)
    except OSError:
        raise InvalidRequest("site: could not resolve host") from None
    addresses = [ip_address(record[4][0]) for record in records]
    resolved = [address for address in addresses if not is_fake_ip(address)]
    if any(not is_public(address) for address in resolved):
        raise InvalidRequest("site: host must resolve only to public addresses")
    if not resolved:
        if not addresses:
            raise InvalidRequest("site: could not resolve host")
        return [
            f"site: resolver returned only fake-IP addresses ({FAKE_IP_NETWORK}); "
            "public address check skipped"
        ]
    return []


def validate_policy(request: Request, phase: Phase, directory: Path) -> None:
    errors = []
    if request.trigger == "musy_lead" and request.musy is None:
        errors.append("musy: required when trigger is musy_lead")
    if request.allow_public_scan and not (request.ben_ok or "").strip():
        errors.append("ben_ok: required when allow_public_scan is true")

    if request.mode == "eval":
        if request.allow_public_scan:
            errors.append("allow_public_scan: public scans are not allowed in eval mode")
        if request.eval_case is None:
            errors.append("eval_case: required when mode is eval")
        if request.fixture_bundle is None:
            errors.append("fixture_bundle: required when mode is eval")
        elif not (directory / request.fixture_bundle).exists():
            errors.append("fixture_bundle: path does not exist")
    elif PHASES.index(request.mode) > PHASES.index(phase):
        errors.append(f"mode: {request.mode} exceeds phase {phase}")

    if errors:
        raise InvalidRequest("\n".join(errors))


def validate_request(path: Path, phase: Phase = "shadow") -> tuple[Request, list[str]]:
    try:
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=RequestLoader)
    except (OSError, UnicodeError) as error:
        raise InvalidRequest(f"could not read request file: {error}") from None
    except InvalidRequest:
        raise
    except (yaml.YAMLError, ValueError, OverflowError) as error:
        mark = getattr(error, "problem_mark", None)
        location = f" at line {mark.line + 1}, column {mark.column + 1}" if mark else ""
        raise InvalidRequest(f"invalid YAML{location}") from None

    try:
        request = Request.model_validate(data)
    except ValidationError as error:
        messages = []
        for item in error.errors(include_input=False):
            field = ".".join(str(part) for part in item["loc"]) or "request"
            messages.append(f"{field}: {item['msg']}")
        raise InvalidRequest("\n".join(messages)) from None

    validate_policy(request, phase, path.parent)
    warnings = validate_site(request.site)
    return request, warnings
