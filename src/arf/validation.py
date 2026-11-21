"""Load and validate a request without fetching its site."""

from ipaddress import IPv6Address, ip_address
from pathlib import Path
import re
import socket
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


HOST_LABEL = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", re.IGNORECASE)


def is_public(address):
    if isinstance(address, IPv6Address):
        if address.is_site_local:
            return False
        if address.ipv4_mapped is not None:
            address = address.ipv4_mapped
    return address.is_global and not address.is_multicast


def validate_site(site: str) -> None:
    invalid_url = "site: must be an HTTPS URL with a valid host and no credentials"
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

    host = hostname.removesuffix(".")
    public_host_required = "site: host must be public"
    if "%" in host:
        raise InvalidRequest(invalid_url)
    try:
        address = ip_address(host)
    except ValueError:
        address = None
    if address is not None:
        if not is_public(address):
            raise InvalidRequest(public_host_required)
        return

    try:
        host = host.encode("idna").decode("ascii").lower()
    except UnicodeError:
        raise InvalidRequest(invalid_url) from None
    if len(host) > 253 or any(not HOST_LABEL.fullmatch(label) for label in host.split(".")):
        raise InvalidRequest(invalid_url)
    if "." not in host or host.endswith(".localhost"):
        raise InvalidRequest(public_host_required)
    try:
        records = socket.getaddrinfo(f"{host}.", None, type=socket.SOCK_STREAM)
    except OSError:
        raise InvalidRequest("site: could not resolve host") from None
    if not records or any(not is_public(ip_address(record[4][0])) for record in records):
        raise InvalidRequest("site: host must resolve only to public addresses")


def validate_policy(request: Request, phase: Phase, directory: Path) -> None:
    errors = []
    if request.trigger == "musy_lead" and request.musy is None:
        errors.append("musy: required when trigger is musy_lead")
    if request.allow_public_scan and not (request.ben_ok or "").strip():
        errors.append("ben_ok: required when allow_public_scan is true")

    if request.mode == "eval":
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


def validate_request(path: Path, phase: Phase = "shadow") -> Request:
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
    validate_site(request.site)
    return request
