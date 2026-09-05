from __future__ import annotations

import re
import shutil
import subprocess
import sys
import tarfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BASH = shutil.which("bash") or "bash"


def _shell_function(source: str, name: str) -> str:
    start = source.index(f"{name}() {{")
    end = source.index("\n}\n", start) + len("\n}\n")
    return source[start:end]


def _bash_path(path: Path) -> str:
    value = path.resolve().as_posix()
    if sys.platform == "win32" and len(value) >= 3 and value[1:3] == ":/":
        return f"/{value[0].lower()}{value[2:]}"
    return value


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
    assert 'Strict-Transport-Security "max-age=31536000" always' in config
    assert "_astro|assets|images|lab/code-kb/assets" in config


def test_systemd_runs_new_source_through_the_reused_virtualenv() -> None:
    service = (ROOT / "deploy" / "codeatlas.service").read_text(encoding="utf-8")

    assert "WorkingDirectory=/opt/codeatlas/backend" in service
    assert (
        "ExecStartPre=/opt/codeatlas/backend/.venv/bin/python -m alembic "
        "-c /opt/codeatlas/backend/alembic.ini upgrade head"
    ) in service
    assert (
        "ExecStart=/opt/codeatlas/backend/.venv/bin/python -m uvicorn "
        "codeatlas.app:create_app --factory"
    ) in service
    assert "/.venv/bin/alembic " not in service
    assert "/.venv/bin/uvicorn " not in service


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
    assert 'PYTHON_BIN=${CODEATLAS_PYTHON_BIN:-python3.12}' in installer
    assert 'command -v "$PYTHON_BIN"' in installer
    assert 'sys.version_info >= (3, 11)' in installer
    assert '"$PYTHON_BIN" "$SOURCE_ROOT/deploy/validate_nginx.py"' in installer
    assert (
        'cmp -s "$BACKEND_TARGET/pyproject.toml" '
        '"$BACKEND_CANDIDATE/pyproject.toml"'
    ) in installer
    assert 'cmp -s "$BACKEND_TARGET/uv.lock" "$BACKEND_CANDIDATE/uv.lock"' in installer
    assert 'rsync -a "$BACKEND_TARGET/.venv/" "$BACKEND_CANDIDATE/.venv/"' in installer
    assert '"$PYTHON_BIN" -I -S -X pycache_prefix="$COMPILE_CACHE" -m compileall' in installer
    assert "PYTHONPYCACHEPREFIX=" not in installer
    assert "COMPILE_CACHE=$(mktemp -d /var/tmp/codeatlas-compile.XXXXXXXX)" in installer
    assert 'rm -rf -- "${COMPILE_CACHE:?}"' in installer
    assert "/var/tmp/codeatlas-compile-cache" not in installer
    assert "ast.parse" in installer
    assert 'runuser --user codeatlas --preserve-environment --' in installer
    assert installer.count('runuser --user codeatlas --preserve-environment --') == 3
    for line in installer.splitlines():
        if ".venv/bin/python -m alembic" in line:
            assert line.startswith("    .venv/bin/python")
    assert '"$PYTHON_BIN" -m venv' not in installer
    assert "pip install" not in installer
    assert 'REVISION_ENV_FILE=/etc/codeatlas/revision.env' in installer
    assert 'RELEASE_MARKER=/opt/codeatlas/RELEASE.json' in installer
    assert '"$SOURCE_ROOT/RELEASE.json"' in installer
    assert 'metadata["commit"] != expected_revision' in installer
    assert 'install -m 0644 "$SOURCE_ROOT/RELEASE.json" "$RELEASE_MARKER"' in installer
    assert 'install -m 0644 "$RELEASE_MARKER" "$NGINX_BACKUP_DIR/RELEASE.json"' in installer
    assert 'install -m 0644 "$NGINX_BACKUP_DIR/RELEASE.json" "$RELEASE_MARKER"' in installer
    assert 'rm -f -- "$RELEASE_MARKER"' in installer
    assert 'python3 "$SOURCE_ROOT/deploy/validate_nginx.py"' not in installer
    assert 'cd "$BACKEND_TARGET"' in installer
    assert ".venv/bin/python -m alembic -c alembic.ini upgrade head" in installer
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
    assert "switch_release()" in installer
    assert "restore_release()" in installer
    assert 'switch_release || fail_nginx_switch "Release switch failed"' in installer
    assert 'BACKUP_SCRIPT="$SOURCE_ROOT/deploy/backup.sh"' in installer
    assert 'BACKUP_ARCHIVE=$("$BACKUP_SCRIPT")' in installer
    assert 'sha256sum -c "$BACKUP_ARCHIVE.sha256"' in installer
    assert 'systemctl stop nginx || fail_nginx_switch' in installer
    assert 'systemctl stop codeatlas || fail_nginx_switch' in installer
    assert "DB_REVISION_BEFORE" in installer
    assert "DB_TARGET_REVISION" in installer
    assert "MIGRATION_STARTED=true" in installer
    assert "MIGRATION_COMPLETED=true" in installer
    assert "rollback_database()" in installer
    assert "dnf module enable" not in installer
    assert "dnf install" not in installer
    assert "MYSQL_CONFIG_CHANGED" not in installer
    assert "for required_command in nginx rsync git mysql mysqldump sha256sum tar" in installer
    assert "flock" in installer
    assert 'MAINTENANCE_LOCK=/run/lock/codeatlas-maintenance.lock' in installer
    assert 'flock -n 9' in installer
    assert "CODEATLAS_MAINTENANCE_LOCK_HELD=1" in installer
    assert 'MYSQL_LOAD_STATE=$(capture_load_state mysqld)' in installer
    assert 'MYSQL_ACTIVE_STATE=$(capture_active_state mysqld)' in installer
    assert '[[ "$MYSQL_LOAD_STATE" == loaded && "$MYSQL_ACTIVE_STATE" == active ]]' in installer
    assert installer.index('systemctl stop nginx || fail_nginx_switch') < installer.index(
        'BACKUP_ARCHIVE=$("$BACKUP_SCRIPT")'
    )
    assert installer.index('systemctl stop codeatlas || fail_nginx_switch') < installer.index(
        'BACKUP_ARCHIVE=$("$BACKUP_SCRIPT")'
    )
    stopped_position = installer.index(
        'verify_active_state codeatlas inactive || fail_nginx_switch'
    )
    active_work_position = installer.index(
        'assert_no_active_work || fail_nginx_switch', stopped_position
    )
    assert stopped_position < active_work_position
    assert active_work_position < installer.index("DB_REVISION_BEFORE=", active_work_position)
    assert active_work_position < installer.index(
        'BACKUP_ARCHIVE=$("$BACKUP_SCRIPT")', active_work_position
    )
    assert active_work_position < installer.index("upgrade head", active_work_position)
    assert installer.index('BACKUP_ARCHIVE=$("$BACKUP_SCRIPT")') < installer.index(
        "upgrade head"
    )
    assert installer.index('switch_release || fail_nginx_switch "Release switch failed"') < (
        installer.index("upgrade head")
    )
    rollback_position = installer.index("rollback_nginx")
    assert installer.index("rollback_database") < installer.index(
        "restore_release", rollback_position
    )
    old_backend_restart = installer.index('systemctl restart codeatlas', rollback_position)
    old_ingress_restart = installer.index('systemctl restart nginx', rollback_position)
    old_health_check = installer.index(
        'curl --fail --silent http://127.0.0.1:8010/api/v1/health',
        rollback_position,
    )
    assert old_backend_restart < old_health_check < old_ingress_restart
    assert installer.index("wait_for_local_health || fail_nginx_switch") < installer.index(
        'systemctl start nginx || fail_public_switch'
    )
    assert 'if [[ "$PUBLIC_EXPOSED" == true ]]' in installer
    assert "TRANSACTION_ACTIVE=true" in installer
    assert "handle_unexpected_error()" in installer
    assert "if (( BASH_SUBSHELL > 0 )); then" in installer
    assert "handle_signal()" in installer
    assert "trap 'handle_unexpected_error' ERR" in installer
    assert "trap 'handle_signal HUP' HUP" in installer
    assert "trap 'handle_signal INT' INT" in installer
    assert "trap 'handle_signal TERM' TERM" in installer
    assert "trap - ERR HUP INT TERM" in installer
    assert installer.index("TRANSACTION_ACTIVE=true") < installer.index(
        'systemctl stop nginx || fail_nginx_switch'
    )
    assert installer.rindex("trap - ERR HUP INT TERM") > installer.index(
        '[[ "$MCP_STATUS" == 401 ]]'
    )
    https_health = installer[
        installer.index("wait_for_https_health()") : installer.index(
            "wait_for_local_health || fail_nginx_switch"
        )
    ]
    assert 'json.load(sys.stdin)["revision"]' in https_health


def test_quiesced_active_work_gate_rejects_work_and_malformed_results() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    function = _shell_function(installer, "assert_no_active_work")
    probe = f"""
set -u
MYSQL_DATABASE=codeatlas
MYSQL_RESULT=$1
mysql() {{ printf '%b' "$MYSQL_RESULT"; }}
{function}
if assert_no_active_work; then
  printf 'ACCEPTED\n'
  exit 0
fi
printf 'REJECTED\n'
exit 7
"""

    cases = {
        "zero": ("0\\t0\\n", 0, "ACCEPTED"),
        "index-running": ("1\\t0\\n", 7, "REJECTED"),
        "external-syncing": ("0\\t2\\n", 7, "REJECTED"),
        "malformed": ("unknown\\n", 7, "REJECTED"),
        "empty-extra-field": ("0\\t0\\t\\n", 7, "REJECTED"),
        "extra-line": ("0\\t0\\n0\\t0\\n", 7, "REJECTED"),
    }
    for name, (mysql_result, expected_code, expected_output) in cases.items():
        result = subprocess.run(
            [BASH, "-c", probe, "probe", mysql_result],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == expected_code, (name, result.stderr)
        assert result.stdout.strip() == expected_output, name


def test_active_state_normalizes_stopped_failed_units_but_rejects_transitions() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    function = _shell_function(installer, "capture_active_state")
    probe = f"""
set -u
SYSTEMD_STATE=$1
systemctl() {{ printf '%s\n' "$SYSTEMD_STATE"; }}
{function}
capture_active_state nginx
"""

    cases = {
        "active": (0, "active"),
        "inactive": (0, "inactive"),
        "failed": (0, "inactive"),
        "activating": (1, ""),
        "deactivating": (1, ""),
        "unknown": (1, ""),
    }
    for state, (expected_code, expected_output) in cases.items():
        result = subprocess.run(
            [BASH, "-c", probe, "probe", state],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
        )
        assert result.returncode == expected_code, (state, result.stderr)
        assert result.stdout.strip() == expected_output, state


def test_release_marker_is_restored_or_removed_by_pre_exposure_rollback(
    tmp_path: Path,
) -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    function = _shell_function(installer, "restore_nginx_files")
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    marker = tmp_path / "RELEASE.json"
    old_marker = '{"commit":"old"}\n'
    new_marker = '{"commit":"new"}\n'
    (backup_dir / "RELEASE.json").write_text(old_marker, encoding="utf-8")

    probe = f"""
set -u
NGINX_TARGET=$1/nginx.conf
NGINX_DEFAULT=$1/default.conf
CODEATLAS_SERVICE=$1/codeatlas.service
REVISION_ENV_FILE=$1/revision.env
RELEASE_MARKER=$1/RELEASE.json
NGINX_BACKUP_DIR=$1/backup
NGINX_TARGET_EXISTED=false
NGINX_DEFAULT_EXISTED=false
CODEATLAS_SERVICE_EXISTED=false
REVISION_ENV_EXISTED=false
RELEASE_MARKER_EXISTED=$2
{function}
restore_nginx_files
"""

    marker.write_text(new_marker, encoding="utf-8")
    restored = subprocess.run(
        [BASH, "-c", probe, "probe", _bash_path(tmp_path), "true"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert restored.returncode == 0, restored.stderr
    assert marker.read_text(encoding="utf-8") == old_marker

    marker.write_text(new_marker, encoding="utf-8")
    removed = subprocess.run(
        [BASH, "-c", probe, "probe", _bash_path(tmp_path), "false"],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )
    assert removed.returncode == 0, removed.stderr
    assert not marker.exists()


def test_backup_and_installer_share_one_maintenance_lock() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    backup = (ROOT / "deploy" / "backup.sh").read_text(encoding="utf-8")

    for script in (installer, backup):
        assert 'MAINTENANCE_LOCK=/run/lock/codeatlas-maintenance.lock' in script
        assert 'flock -n 9' in script
    assert "CODEATLAS_MAINTENANCE_LOCK_HELD=1" in installer
    assert '[[ ${CODEATLAS_MAINTENANCE_LOCK_HELD:-0} != 1 ]]' in backup
    assert backup.index('flock -n 9') < backup.index(
        'STAGE=$(mktemp -d "$BACKUP_DIR/.stage-${STAMP}-XXXXXXXX")'
    )
    assert 'STAGE="$BACKUP_DIR/.stage-$STAMP"' not in backup
    assert 'ARCHIVE="$BACKUP_DIR/codeatlas-$BACKUP_ID.tar.gz"' in backup


def test_restore_stage_rejects_a_bad_checksum_in_conditional_context(
    tmp_path: Path,
) -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    function = _shell_function(installer, "ensure_restore_stage").replace(
        "restore_candidate=$(mktemp -d /var/backups/codeatlas/restore-XXXXXXXX)",
        'restore_candidate=$(mktemp -d "$RESTORE_TEMPLATE")',
    )
    assert 'restore_candidate=$(mktemp -d "$RESTORE_TEMPLATE")' in function
    payload = tmp_path / "payload"
    payload.mkdir()
    (payload / "codeatlas.sql").write_text("SELECT 1;\n", encoding="utf-8")
    archive = tmp_path / "backup.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        bundle.add(payload / "codeatlas.sql", arcname="codeatlas.sql")
    Path(f"{archive}.sha256").write_text("invalid checksum\n", encoding="utf-8")
    probe = f"""
set -u
BACKUP_ARCHIVE=$1
RESTORE_TEMPLATE=$2
RESTORE_STAGE=''
{function}
if ensure_restore_stage; then
  printf 'UNSAFE_ACCEPT\n'
  exit 0
fi
printf 'CHECKSUM_REJECTED\n'
exit 7
"""

    result = subprocess.run(
        [
            BASH,
            "-c",
            probe,
            "probe",
            _bash_path(archive),
            _bash_path(tmp_path / "restore-XXXXXXXX"),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 7, result.stderr
    assert result.stdout.strip() == "CHECKSUM_REJECTED"


def test_database_rollback_rejects_a_restored_revision_mismatch(tmp_path: Path) -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    function = _shell_function(installer, "rollback_database")
    (tmp_path / "codeatlas.sql").write_text("SELECT 1;\n", encoding="utf-8")
    probe = f"""
set -u
MIGRATION_STARTED=true
MYSQL_DATABASE=codeatlas_test
RESTORE_STAGE=$1
DB_REVISION_BEFORE=expected_revision
DB_TARGET_REVISION=new_revision
MIGRATION_COMPLETED=true
ensure_restore_stage() {{ return 0; }}
mysql() {{
  local argument
  for argument in "$@"; do
    if [[ "$argument" == -e ]]; then
      printf 'wrong_revision\n'
      return 0
    fi
  done
  cat >/dev/null
}}
{function}
if rollback_database; then
  printf 'UNSAFE_ACCEPT\n'
  exit 0
fi
printf 'REVISION_REJECTED\n'
exit 7
"""

    result = subprocess.run(
        [BASH, "-c", probe, "probe", _bash_path(tmp_path)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 7, result.stderr
    assert result.stdout.strip() == "REVISION_REJECTED"


def test_installer_resolves_the_complete_migration_graph_without_importing_it() -> None:
    installer = (ROOT / "deploy" / "install.sh").read_text(encoding="utf-8")
    marker = 'DB_TARGET_REVISION=$("$PYTHON_BIN" -I -S - '
    block = installer[installer.index(marker) :]
    parser = block.split("<<'PY'\n", 1)[1].split("\nPY\n", 1)[0]

    result = subprocess.run(
        [
            sys.executable,
            "-I",
            "-S",
            "-",
            str(ROOT / "backend" / "alembic" / "versions"),
        ],
        input=parser,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "20260904_16"


def test_bash_err_trap_defers_recovery_from_subshell_to_parent() -> None:
    probe = r"""
set -Eeuo pipefail
recover() {
  local status=$?
  if (( BASH_SUBSHELL > 0 )); then
    exit "$status"
  fi
  trap - ERR
  printf 'recover pid=%s subshell=%s\n' "$BASHPID" "$BASH_SUBSHELL"
  exit "$status"
}
fail_in_subshell() ( false )
trap 'recover' ERR
fail_in_subshell
"""

    result = subprocess.run(
        [BASH, "-c", probe],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )

    assert result.returncode != 0
    recoveries = result.stdout.splitlines()
    assert len(recoveries) == 1
    assert "subshell=0" in recoveries[0]


def test_ci_does_not_hold_a_production_deployment_path() -> None:
    assert not (ROOT / ".github" / "workflows" / "deploy.yml").exists()
    workflow = (ROOT / ".github" / "workflows" / "ci.yml").read_text(
        encoding="utf-8"
    )

    assert "DEPLOY_SSH_KEY" not in workflow
    assert "workflow_run:" not in workflow
    assert "upload-artifact" not in workflow
    for reference in re.findall(r"uses:\s*[^@\s]+@([^\s#]+)", workflow):
        assert re.fullmatch(r"[0-9a-f]{40}", reference)


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
