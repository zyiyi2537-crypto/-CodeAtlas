from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_nginx_template_never_proxies_application_over_http() -> None:
    config = (ROOT / "deploy" / "nginx-codeatlas.conf").read_text(encoding="utf-8")
    http_block, https_block = config.split("server {", maxsplit=2)[1:]

    assert "listen 80" in http_block
    assert "/.well-known/acme-challenge/" in http_block
    assert "return 308 https://" in http_block
    assert "proxy_pass" not in http_block
    assert "try_files" not in http_block
    assert "listen 8080" not in config

    assert "listen 443 ssl" in https_block
    assert "ssl_certificate" in https_block
    assert "proxy_pass http://127.0.0.1:8010/api/v1/" in https_block
    assert "codeatlas-allowlist" not in config


def test_installer_fails_closed_without_https_and_migrates_old_allowlist() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")

    assert "CODEATLAS_PUBLIC_ORIGIN" in installer
    assert "https://" in installer
    assert "CODEATLAS_COOKIE_SECURE" in installer
    assert "nginx -t" in installer
    assert "codeatlas-allowlist" in installer
    assert "configure the public domain and HTTPS" not in installer
    assert '"https://$CODEATLAS_DOMAIN/api/code-kb/health"' in installer
    assert "-g codeatlas" in installer
    assert "deploy/validate_nginx.py" in installer
    assert 'nginx -t -c "$NGINX_PREFLIGHT"' in installer
    assert installer.index('nginx -t -c "$NGINX_PREFLIGHT"') < installer.index(
        'rsync -a --delete \\\n'
    )
    assert installer.index('nginx -t -c "$NGINX_PREFLIGHT"') < installer.index(
        "upgrade head"
    )
    assert 'NGINX_DEFAULT_EXISTED=false' in installer
    assert 'install -m 0644 "$NGINX_DEFAULT" "$NGINX_BACKUP_DIR/default.conf"' in installer
    assert 'install -m 0644 "$NGINX_BACKUP_DIR/default.conf" "$NGINX_DEFAULT"' in installer
    assert 'systemctl daemon-reload || fail_nginx_switch' in installer
    assert 'systemctl enable nginx codeatlas || fail_nginx_switch' in installer
    assert 'CODEATLAS_MCP_ALLOWED_HOSTS must include the bare host' in installer
    assert "capture_active_state()" in installer
    assert "capture_enabled_state()" in installer
    assert "capture_load_state()" in installer
    assert 'CODEATLAS_ENABLED_STATE=absent' in installer
    assert 'CODEATLAS_ACTIVE_STATE=inactive' in installer
    assert 'verify_active_state()' in installer
    assert 'verify_load_state()' in installer
    assert 'verify_enabled_state()' in installer
    assert 'is-active --quiet' not in installer
    assert 'enabled-runtime' not in installer
    assert 'masked-runtime' not in installer
    assert 'linked-runtime' not in installer
    assert "static|indirect" not in installer
    assert "systemctl mask" not in installer
    assert 'NGINX_LOAD_STATE=$(capture_load_state nginx)' in installer
    assert 'CODEATLAS_LOAD_STATE=$(capture_load_state codeatlas)' in installer
    assert 'CodeAtlas unit file exists but systemd reports LoadState=not-found' in installer
    assert 'current_codeatlas_load=$(capture_load_state codeatlas)' in installer
    assert '! verify_enabled_state nginx "$NGINX_ENABLED_STATE"' in installer
    assert '! verify_load_state codeatlas not-found' in installer
    assert installer.index("NGINX_ACTIVE_STATE=$(capture_active_state nginx)") < (
        installer.index('rsync -a --delete \\\n')
    )
    assert 'allowed_host//[[:space:]]/' not in installer
    assert 'trim_edges "$allowed_host"' in installer
    candidate_install = 'install -m 0644 "$NGINX_CANDIDATE" "$NGINX_TARGET" \\\n'
    assert candidate_install in installer
    assert installer.index('fail_nginx_switch "Nginx candidate installation failed"') > (
        installer.index(candidate_install)
    )
    assert 'rm -f -- "$NGINX_DEFAULT" || fail_nginx_switch' in installer
    assert 'systemctl reload nginx || true' not in installer
    assert 'systemctl stop nginx || true' not in installer
    assert "ROLLBACK FAILED" in installer


def test_nginx_validator_accepts_acme_and_plaintext_redirects(tmp_path: Path) -> None:
    config = (ROOT / "deploy" / "nginx-codeatlas.conf").read_text(encoding="utf-8")
    config = config.replace("CODEATLAS_DOMAIN", "codeatlas.example.com")
    config = config.replace(
        "        allow all;",
        "        try_files $uri =404;\n        allow all;",
    )
    config += """
server {
    listen 8080;
    server_name _;
    return 308 https://codeatlas.example.com$request_uri;
}
"""
    candidate = tmp_path / "safe.conf"
    candidate.write_text(config, encoding="utf-8")

    result = _validate(candidate)

    assert result.returncode == 0, result.stderr


def test_nginx_validator_rejects_plaintext_application_routes(tmp_path: Path) -> None:
    base = (ROOT / "deploy" / "nginx-codeatlas.conf").read_text(encoding="utf-8")
    base = base.replace("CODEATLAS_DOMAIN", "codeatlas.example.com")
    unsafe_cases = {
        "http-proxy": _inject_http_location(
            base, "location /unsafe { proxy_pass http://127.0.0.1:8010; }"
        ),
        "http-static": _inject_http_location(
            base, "location /unsafe { root /var/www/codeatlas; }"
        ),
        "http-return": _inject_http_location(
            base, "location /unsafe { return 200 plaintext; }"
        ),
        "http-include": _inject_http_location(
            base, "include /etc/nginx/conf.d/unsafe-routes.conf;"
        ),
        "alternate-bind": base.replace(
            "listen 80 default_server;",
            "listen 80 default_server;\n    listen 0.0.0.0:8888;",
            1,
        ),
        "loopback-only-http": base.replace(
            "listen 80 default_server;",
            "listen 127.0.0.1:80 default_server;",
            1,
        ).replace(
            "listen [::]:80 default_server;",
            "listen [::1]:80 default_server;",
            1,
        ),
        "loopback-only-https": base.replace(
            "listen 443 ssl default_server;",
            "listen 127.0.0.1:443 ssl default_server;",
            1,
        ).replace(
            "listen [::]:443 ssl default_server;",
            "listen [::1]:443 ssl default_server;",
            1,
        ),
        "wrong-http-domain": base.replace(
            "server_name codeatlas.example.com;",
            "server_name wrong.example.com;",
            1,
        ),
        "mixed-http-legacy-listener": base.replace(
            "listen 80 default_server;",
            "listen 80 default_server;\n    listen 8080;",
            1,
        ),
        "duplicate-http-server": base
        + "\nserver { listen 80; server_name codeatlas.example.com; "
        + "location ^~ /.well-known/acme-challenge/ { root /var/lib/letsencrypt; } "
        + "location / { return 308 https://codeatlas.example.com$request_uri; } }",
        "server-return-before-acme": base.replace(
            "    location / {\n"
            "        return 308 https://codeatlas.example.com$request_uri;\n"
            "    }",
            "    return 308 https://codeatlas.example.com$request_uri;",
            1,
        ),
        "alternate-port": base
        + "\nserver { listen 8080; location / { try_files $uri =404; } "
        + "return 308 https://codeatlas.example.com$request_uri; }",
    }
    for name, unsafe_config in unsafe_cases.items():
        candidate = tmp_path / f"{name}.conf"
        candidate.write_text(unsafe_config, encoding="utf-8")

        result = _validate(candidate)

        assert result.returncode == 1, name


def test_nginx_validator_rejects_tls_policy_drift(tmp_path: Path) -> None:
    base = (ROOT / "deploy" / "nginx-codeatlas.conf").read_text(encoding="utf-8")
    base = base.replace("CODEATLAS_DOMAIN", "codeatlas.example.com")
    unsafe_cases = {
        "tls-deny": base.replace(
            "    client_max_body_size 25m;",
            "    client_max_body_size 25m;\n    deny all;",
            1,
        ),
        "tls-location": base.replace(
            "    location / {",
            "    location /admin-only { return 200 hidden; }\n\n    location / {",
            1,
        ),
        "tls-extra-name": base.replace(
            "server_name codeatlas.example.com;",
            "server_name codeatlas.example.com evil.example;",
            2,
        ),
        "tls-listen-option": base.replace(
            "listen 443 ssl default_server;",
            "listen 443 ssl default_server proxy_protocol;",
            1,
        ),
    }
    for name, unsafe_config in unsafe_cases.items():
        candidate = tmp_path / f"{name}.conf"
        candidate.write_text(unsafe_config, encoding="utf-8")

        result = _validate(candidate)

        assert result.returncode == 1, name


def _inject_http_location(config: str, location: str) -> str:
    marker = "    location / {\n        return 308 https://"
    assert marker in config
    return config.replace(marker, f"    {location}\n\n{marker}", 1)


def _validate(config: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            str(ROOT / "deploy" / "validate_nginx.py"),
            str(config),
            "codeatlas.example.com",
        ],
        check=False,
        capture_output=True,
        text=True,
    )
