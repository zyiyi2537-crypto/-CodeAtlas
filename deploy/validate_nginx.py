#!/usr/bin/env python3
from __future__ import annotations

import re
import sys
from pathlib import Path


class NginxValidationError(ValueError):
    pass


def _remove_comments(content: str) -> str:
    result: list[str] = []
    quote = ""
    escaped = False
    comment = False
    for char in content:
        if comment:
            if char == "\n":
                comment = False
                result.append(char)
            continue
        if escaped:
            result.append(char)
            escaped = False
            continue
        if char == "\\":
            result.append(char)
            escaped = True
            continue
        if quote:
            result.append(char)
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
            result.append(char)
        elif char == "#":
            comment = True
        else:
            result.append(char)
    return "".join(result)


def _matching_brace(content: str, opening: int) -> int:
    depth = 1
    quote = ""
    escaped = False
    for index in range(opening + 1, len(content)):
        char = content[index]
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in {'"', "'"}:
            quote = char
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return index + 1
    raise NginxValidationError("Nginx block is unbalanced")


def _entries(content: str) -> list[tuple[str, str, str]]:
    """Parse directives and nested blocks at exactly one Nginx context level."""
    result: list[tuple[str, str, str]] = []
    index = 0
    while index < len(content):
        while index < len(content) and content[index].isspace():
            index += 1
        if index == len(content):
            break
        start = index
        quote = ""
        escaped = False
        while index < len(content):
            char = content[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif quote:
                if char == quote:
                    quote = ""
            elif char in {'"', "'"}:
                quote = char
            elif char == ";":
                statement = content[start:index].strip()
                if not statement:
                    raise NginxValidationError("Empty Nginx directive")
                result.append(("statement", statement, ""))
                index += 1
                break
            elif char == "{":
                header = content[start:index].strip()
                if not header:
                    raise NginxValidationError("Nginx block has no name")
                end = _matching_brace(content, index)
                result.append(("block", header, content[index + 1 : end - 1]))
                index = end
                break
            elif char == "}":
                raise NginxValidationError("Unexpected closing brace")
            index += 1
        else:
            trailing = content[start:].strip()
            if trailing:
                raise NginxValidationError(f"Unterminated Nginx directive: {trailing}")
    return result


def _directive(statement: str) -> tuple[str, str]:
    parts = statement.split(None, 1)
    return parts[0].lower(), parts[1].strip() if len(parts) == 2 else ""


def _parse_listen(value: str) -> tuple[str, int, set[str]]:
    normalized = " ".join(value.split())
    match = re.fullmatch(
        r"(?:(?P<address>(?:\d{1,3}\.){3}\d{1,3}|\[[0-9a-fA-F:.]+\]):)?"
        r"(?P<port>\d+)(?:\s+(?P<options>.*))?",
        normalized,
    )
    if not match:
        raise NginxValidationError(f"Unsupported listen address: {value}")
    options = set((match.group("options") or "").lower().split())
    return match.group("address") or "", int(match.group("port")), options


def _validate_redirect(value: str, expected_domain: str) -> None:
    expected = f"308 https://{expected_domain}$request_uri"
    if " ".join(value.split()) != expected:
        raise NginxValidationError(
            f"Plaintext traffic must redirect exactly to https://{expected_domain}"
        )


def _validate_acme_location(body: str) -> None:
    seen: set[str] = set()
    allowed = {
        "root": "/var/lib/letsencrypt",
        "default_type": "text/plain",
        "try_files": "$uri =404",
        "allow": "all",
    }
    for kind, statement, _nested in _entries(body):
        if kind != "statement":
            raise NginxValidationError("ACME location cannot contain nested blocks")
        name, value = _directive(statement)
        if name not in allowed or " ".join(value.split()) != allowed[name]:
            raise NginxValidationError(f"Unsafe ACME directive: {statement}")
        if name in seen:
            raise NginxValidationError(f"Duplicate ACME directive: {name}")
        seen.add(name)
    if "root" not in seen:
        raise NginxValidationError("ACME location must use /var/lib/letsencrypt")


def _validate_redirect_location(body: str, expected_domain: str) -> None:
    entries = _entries(body)
    if len(entries) != 1 or entries[0][0] != "statement":
        raise NginxValidationError("Plaintext redirect location must contain one return")
    name, value = _directive(entries[0][1])
    if name != "return":
        raise NginxValidationError("Plaintext redirect location must contain one return")
    _validate_redirect(value, expected_domain)


def _validate_plaintext_server(
    body: str,
    ports: set[int],
    expected_domain: str,
) -> tuple[bool, bool]:
    has_redirect = False
    has_acme = False
    redirect_in_location = False
    for kind, item, nested in _entries(body):
        if kind == "statement":
            name, value = _directive(item)
            if name == "listen":
                continue
            if name == "server_name" and value:
                continue
            if name == "server_tokens" and value.lower() == "off":
                continue
            if name == "return":
                if has_redirect:
                    raise NginxValidationError("Plaintext redirect is duplicated")
                _validate_redirect(value, expected_domain)
                has_redirect = True
                continue
            raise NginxValidationError(f"Unsafe plaintext directive: {item}")

        header = " ".join(item.split())
        if re.fullmatch(
            r"location (?:\^~ )?/\.well-known/acme-challenge/",
            header,
            flags=re.IGNORECASE,
        ):
            if ports != {80} or has_acme:
                raise NginxValidationError("ACME location is duplicated or not on port 80")
            _validate_acme_location(nested)
            has_acme = True
        elif header.lower() == "location /":
            if has_redirect:
                raise NginxValidationError("Plaintext redirect is duplicated")
            _validate_redirect_location(nested, expected_domain)
            has_redirect = True
            redirect_in_location = True
        else:
            raise NginxValidationError(f"Unsafe plaintext block: {header}")

    if not has_redirect:
        raise NginxValidationError(
            f"Plaintext listener {sorted(ports)} must redirect to HTTPS"
        )
    if 80 in ports and has_acme and not redirect_in_location:
        raise NginxValidationError(
            "Port 80 ACME traffic requires the redirect inside location /"
        )
    return 80 in ports, has_acme


def _normalized_statement_set(body: str) -> tuple[set[str], int]:
    statements: list[str] = []
    for kind, item, _nested in _entries(body):
        if kind == "statement" and _directive(item)[0] != "listen":
            statements.append(" ".join(item.split()))
    return set(statements), len(statements)


def _validate_exact_location(body: str, expected: set[str]) -> None:
    entries = _entries(body)
    if any(kind != "statement" for kind, _item, _nested in entries):
        raise NginxValidationError("TLS application locations cannot nest blocks")
    statements = [" ".join(item.split()) for _kind, item, _nested in entries]
    if set(statements) != expected or len(statements) != len(expected):
        raise NginxValidationError("TLS application location differs from the safe template")


def _canonical_tls_statements(expected_domain: str) -> set[str]:
    content_security_policy = (
        'add_header Content-Security-Policy "default-src \'self\'; '
        "connect-src 'self'; img-src 'self' data:; style-src 'self' 'unsafe-inline'; "
        "font-src 'self'; frame-ancestors 'none'; base-uri 'self'; "
        "form-action 'self'\" always"
    )
    gzip_types = (
        "gzip_types text/plain text/css application/javascript application/json "
        "application/xml image/svg+xml"
    )
    return {
        f"server_name {expected_domain}",
        "root /var/www/codeatlas",
        "index index.html",
        "server_tokens off",
        "client_max_body_size 25m",
        f"ssl_certificate /etc/letsencrypt/live/{expected_domain}/fullchain.pem",
        f"ssl_certificate_key /etc/letsencrypt/live/{expected_domain}/privkey.pem",
        "ssl_protocols TLSv1.2 TLSv1.3",
        "ssl_session_cache shared:CodeAtlasSSL:10m",
        "ssl_session_timeout 1d",
        "ssl_session_tickets off",
        'add_header X-Content-Type-Options "nosniff" always',
        'add_header X-Frame-Options "DENY" always',
        'add_header Strict-Transport-Security "max-age=31536000" always',
        'add_header Referrer-Policy "strict-origin-when-cross-origin" always',
        'add_header Permissions-Policy "camera=(), microphone=(), geolocation=()" always',
        content_security_policy,
        "gzip on",
        gzip_types,
    }


def _canonical_tls_locations() -> dict[str, set[str]]:
    immutable_asset_location = (
        'location ~* "^/(_astro|assets|images|lab/code-kb/assets)/.+\\.[a-f0-9_-]{8,}'
        '\\.(css|js|png|jpg|jpeg|webp|svg|woff2)$"'
    )
    common_proxy = {
        "proxy_http_version 1.1",
        "proxy_set_header Host $host",
        "proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for",
        "proxy_set_header X-Forwarded-Proto $scheme",
    }
    return {
        "location ^~ /api/code-kb/": common_proxy
        | {
            "proxy_pass http://127.0.0.1:8010/api/v1/",
            "proxy_set_header X-Real-IP $remote_addr",
            "proxy_read_timeout 120s",
        },
        "location = /mcp": common_proxy
        | {
            "proxy_pass http://127.0.0.1:8010/mcp/",
            "proxy_set_header Authorization $http_authorization",
            "proxy_buffering off",
            "proxy_read_timeout 3600s",
        },
        "location ^~ /mcp/": common_proxy
        | {
            "proxy_pass http://127.0.0.1:8010/mcp/",
            "proxy_set_header Authorization $http_authorization",
            "proxy_buffering off",
            "proxy_request_buffering off",
            "proxy_read_timeout 3600s",
        },
        "location ^~ /lab/code-kb/": {
            "try_files $uri $uri/ /lab/code-kb/index.html"
        },
        immutable_asset_location: {
            "expires 30d",
            'add_header Cache-Control "public, immutable"',
            "try_files $uri =404",
        },
        "location /": {"try_files $uri $uri/ $uri/index.html =404"},
    }


def _validate_canonical_tls_server(body: str, expected_domain: str) -> None:
    statements, count = _normalized_statement_set(body)
    expected_statements = _canonical_tls_statements(expected_domain)
    if statements != expected_statements or count != len(expected_statements):
        raise NginxValidationError("Canonical TLS directives differ from the safe template")

    expected_locations = _canonical_tls_locations()
    seen: set[str] = set()
    for kind, item, nested in _entries(body):
        if kind != "block":
            continue
        header = " ".join(item.split())
        expected = expected_locations.get(header)
        if expected is None or header in seen:
            raise NginxValidationError(f"Unsafe or duplicate TLS block: {header}")
        _validate_exact_location(nested, expected)
        seen.add(header)
    if seen != set(expected_locations):
        raise NginxValidationError("Canonical TLS server is missing a required location")


def _validate_redirect_tls_server(body: str, expected_domain: str) -> None:
    entries = _entries(body)
    if any(kind != "statement" for kind, _item, _nested in entries):
        raise NginxValidationError("TLS redirect servers cannot contain locations")
    server_name_statements = [
        value
        for _kind, item, _nested in entries
        for name, value in [_directive(item)]
        if name == "server_name"
    ]
    if len(server_name_statements) != 1:
        raise NginxValidationError("TLS redirect server must declare one server_name")
    aliases = set(server_name_statements[0].split())
    if not aliases or expected_domain in aliases or any(
        not alias.endswith(f".{expected_domain}") for alias in aliases
    ):
        raise NginxValidationError("TLS redirect names must be subdomains of the canonical domain")
    expected = {
        f"server_name {server_name_statements[0]}",
        f"ssl_certificate /etc/letsencrypt/live/{expected_domain}/fullchain.pem",
        f"ssl_certificate_key /etc/letsencrypt/live/{expected_domain}/privkey.pem",
        "ssl_protocols TLSv1.2 TLSv1.3",
        "ssl_session_cache shared:CodeAtlasSSL:10m",
        "ssl_session_timeout 1d",
        "ssl_session_tickets off",
        f"return 308 https://{expected_domain}$request_uri",
    }
    statements, count = _normalized_statement_set(body)
    if statements != expected or count != len(expected):
        raise NginxValidationError("TLS redirect directives differ from the safe template")


def validate_nginx_config(content: str, expected_domain: str) -> None:
    content = _remove_comments(content)
    if "CODEATLAS_DOMAIN" in content:
        raise NginxValidationError("Nginx configuration still contains a domain placeholder")
    if re.search(r"\binclude\s+", content, flags=re.IGNORECASE):
        raise NginxValidationError("Nginx configuration cannot include unreviewed directives")
    if re.search(r"codeatlas-allowlist", content, flags=re.IGNORECASE):
        raise NginxValidationError("Nginx configuration still references the retired allowlist")

    top_level = _entries(content)
    if not top_level:
        raise NginxValidationError("Nginx configuration has no server block")
    if any(
        kind != "block" or " ".join(item.split()).lower() != "server"
        for kind, item, _ in top_level
    ):
        raise NginxValidationError("CodeAtlas Nginx file may contain only server blocks")

    http_server_count = 0
    acme_server_count = 0
    canonical_tls_count = 0
    for _kind, _header, body in top_level:
        direct_entries = _entries(body)
        listens = [
            _parse_listen(value)
            for kind, statement, _nested in direct_entries
            if kind == "statement"
            for name, value in [_directive(statement)]
            if name == "listen"
        ]
        if not listens:
            raise NginxValidationError("Every CodeAtlas server block must declare a port")
        ports = {port for _address, port, _options in listens}
        unsupported = ports - {80, 443, 8080}
        if unsupported:
            raise NginxValidationError(
                f"Unsupported plaintext or alternate listener: {sorted(unsupported)}"
            )
        plaintext_ports = ports & {80, 8080}
        if plaintext_ports and 443 in ports:
            raise NginxValidationError("TLS and plaintext listeners must use separate blocks")
        if plaintext_ports == {80, 8080}:
            raise NginxValidationError("Ports 80 and 8080 must use separate server blocks")

        public_addresses = {"", "0.0.0.0", "[::]"}
        if any(address not in public_addresses for address, _port, _options in listens):
            raise NginxValidationError("CodeAtlas listeners must use public wildcard binds")

        if 443 in ports:
            allowed_tls_options = {
                "ssl",
                "default_server",
                "reuseport",
                "ipv6only=on",
            }
            for _address, port, options in listens:
                if port == 443 and (
                    "ssl" not in options or not options <= allowed_tls_options
                ):
                    raise NginxValidationError("Port 443 has unsafe TLS listen options")
            server_names = re.findall(r"\bserver_name\s+([^;]+);", body)
            if len(server_names) != 1:
                raise NginxValidationError("Every TLS server must declare one server_name")
            names = {
                name
                for values in server_names
                for name in re.split(r"\s+", values.strip())
                if name
            }
            if expected_domain in names:
                if names != {expected_domain}:
                    raise NginxValidationError(
                        "The canonical TLS server cannot contain extra names"
                    )
                _validate_canonical_tls_server(body, expected_domain)
                canonical_tls_count += 1
            else:
                _validate_redirect_tls_server(body, expected_domain)

        if plaintext_ports:
            allowed_plaintext_options = {"default_server", "reuseport", "ipv6only=on"}
            for _address, port, options in listens:
                if port in {80, 8080} and not options <= allowed_plaintext_options:
                    raise NginxValidationError(
                        f"Unsafe plaintext listen options: {sorted(options)}"
                    )
            if 80 in plaintext_ports:
                server_names = re.findall(r"\bserver_name\s+([^;]+);", body)
                names = {
                    name
                    for values in server_names
                    for name in re.split(r"\s+", values.strip())
                    if name
                }
                if expected_domain not in names:
                    raise NginxValidationError(
                        f"Port 80 server_name must include {expected_domain}"
                    )
            block_has_http, block_has_acme = _validate_plaintext_server(
                body,
                plaintext_ports,
                expected_domain,
            )
            http_server_count += int(block_has_http)
            acme_server_count += int(block_has_acme)

    if canonical_tls_count != 1:
        raise NginxValidationError(
            f"Nginx configuration must have exactly one TLS server for {expected_domain}"
        )
    if http_server_count != 1 or acme_server_count != 1:
        raise NginxValidationError(
            "Exactly one port-80 server must provide ACME and redirect all other traffic"
        )


def main() -> int:
    if len(sys.argv) != 3:
        print("usage: validate_nginx.py CONFIG EXPECTED_DOMAIN", file=sys.stderr)
        return 2
    try:
        content = Path(sys.argv[1]).read_text(encoding="utf-8")
        validate_nginx_config(content, sys.argv[2])
    except (OSError, NginxValidationError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
