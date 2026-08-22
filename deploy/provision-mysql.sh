#!/usr/bin/env bash
set -euo pipefail

ENV_FILE=${CODEATLAS_ENV_FILE:-/etc/codeatlas/codeatlas.env}
MYSQL_DATABASE=${CODEATLAS_MYSQL_DATABASE:-codeatlas}
MYSQL_USER=${CODEATLAS_MYSQL_USER:-codeatlas}
MYSQL_PASSWORD=${CODEATLAS_MYSQL_PASSWORD:-}

if [[ $(id -u) -ne 0 ]]; then
  echo "Run this provisioner as root" >&2
  exit 1
fi
if ! getent group codeatlas >/dev/null 2>&1; then
  echo "Create the codeatlas system user before provisioning MySQL" >&2
  exit 1
fi
if [[ ! "$MYSQL_DATABASE" =~ ^[A-Za-z0-9_]+$ || ! "$MYSQL_USER" =~ ^[A-Za-z0-9_]+$ ]]; then
  echo "Database and user names may contain only letters, digits, and underscores" >&2
  exit 1
fi
if [[ ! "$MYSQL_PASSWORD" =~ ^[A-Za-z0-9._~-]{24,128}$ ]]; then
  echo "Set CODEATLAS_MYSQL_PASSWORD to a 24-128 character URL-safe password" >&2
  exit 1
fi

mysql_root=(mysql --protocol=socket --user=root)
if [[ -n "${MYSQL_ROOT_PASSWORD:-}" ]]; then
  export MYSQL_PWD=$MYSQL_ROOT_PASSWORD
fi
"${mysql_root[@]}" <<SQL
CREATE DATABASE IF NOT EXISTS \`$MYSQL_DATABASE\`
  CHARACTER SET utf8mb4 COLLATE utf8mb4_0900_ai_ci;
CREATE USER IF NOT EXISTS '$MYSQL_USER'@'127.0.0.1'
  IDENTIFIED BY '$MYSQL_PASSWORD';
ALTER USER '$MYSQL_USER'@'127.0.0.1'
  IDENTIFIED BY '$MYSQL_PASSWORD';
GRANT ALL PRIVILEGES ON \`$MYSQL_DATABASE\`.* TO '$MYSQL_USER'@'127.0.0.1';
FLUSH PRIVILEGES;
SQL

root_auth=$("${mysql_root[@]}" --skip-column-names --silent -e \
  "SELECT CONCAT(plugin, ':', LENGTH(authentication_string))
   FROM mysql.user WHERE User = 'root' AND Host = 'localhost'")
if [[ "$root_auth" == "mysql_native_password:0" || \
      "$root_auth" == "caching_sha2_password:0" ]]; then
  if ! "${mysql_root[@]}" --skip-column-names --silent -e \
    "SELECT PLUGIN_NAME FROM INFORMATION_SCHEMA.PLUGINS
     WHERE PLUGIN_NAME = 'auth_socket'" | grep -qx auth_socket; then
    "${mysql_root[@]}" -e "INSTALL PLUGIN auth_socket SONAME 'auth_socket.so'"
  fi
  "${mysql_root[@]}" -e \
    "ALTER USER 'root'@'localhost' IDENTIFIED WITH auth_socket"
fi
unset MYSQL_PWD

install -d -m 0750 "$(dirname "$ENV_FILE")"
touch "$ENV_FILE"
chmod 0640 "$ENV_FILE"
chown root:codeatlas "$ENV_FILE"

database_url="mysql+pymysql://$MYSQL_USER:$MYSQL_PASSWORD@127.0.0.1:3306/$MYSQL_DATABASE?charset=utf8mb4"
temporary=$(mktemp "$(dirname "$ENV_FILE")/.codeatlas-env.XXXXXX")
awk -v value="$database_url" '
  BEGIN { replaced = 0 }
  /^CODEATLAS_DATABASE_URL=/ {
    if (!replaced) print "CODEATLAS_DATABASE_URL=" value
    replaced = 1
    next
  }
  { print }
  END { if (!replaced) print "CODEATLAS_DATABASE_URL=" value }
' "$ENV_FILE" > "$temporary"
install -m 0640 -o root -g codeatlas "$temporary" "$ENV_FILE"
rm -f -- "$temporary"

echo "Provisioned MySQL database $MYSQL_DATABASE for CodeAtlas"
