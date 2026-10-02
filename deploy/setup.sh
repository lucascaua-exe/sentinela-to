#!/usr/bin/env bash
# Provisiona (ou atualiza) o Sentinela TO num servidor Debian/Ubuntu.
# Uso: sudo bash deploy/setup.sh   (executado a partir da pasta do projeto já copiada para APP_DIR)
set -euo pipefail

APP_DIR=/opt/sentinela-to
APP_USER=sentinela
ENV_FILE=/etc/sentinela-to.env
PYTHON_VERSION=3.12

export DEBIAN_FRONTEND=noninteractive NEEDRESTART_MODE=a NEEDRESTART_SUSPEND=1

echo "==> Pacotes do sistema"
apt-get update -qq
apt-get install -y -qq curl ca-certificates nginx >/dev/null

if ! id "$APP_USER" >/dev/null 2>&1; then
  useradd --system --home "$APP_DIR" --shell /usr/sbin/nologin "$APP_USER"
fi

echo "==> Python $PYTHON_VERSION e dependências (uv)"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin INSTALLER_NO_MODIFY_PATH=1 sh >/dev/null
fi
# numpy 1.26 não tem binários para Python 3.13+, por isso o venv usa um Python 3.12 gerenciado pelo uv.
export UV_PYTHON_INSTALL_DIR=/opt/uv-python
uv venv --quiet --allow-existing --python "$PYTHON_VERSION" "$APP_DIR/.venv"
uv pip install --quiet --python "$APP_DIR/.venv/bin/python" -r "$APP_DIR/requirements.txt"

echo "==> Configuração"
if [ ! -f "$ENV_FILE" ]; then
  ADMIN_PASSWORD="$(openssl rand -base64 18 | tr -dc 'A-Za-z0-9' | head -c 16)"
  cat >"$ENV_FILE" <<EOF
SENTINELA_ADMIN_USER=admin
SENTINELA_ADMIN_PASSWORD=$ADMIN_PASSWORD
SENTINELA_DEVICE_TOKEN=$(openssl rand -hex 24)
SENTINELA_DB_PATH=$APP_DIR/data/sentinela.db
EOF
  chmod 600 "$ENV_FILE"
fi

mkdir -p "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR/data"
chmod 750 "$APP_DIR/data"

echo "==> Serviços"
install -m 644 "$APP_DIR/deploy/sentinela-api.service" /etc/systemd/system/sentinela-api.service
install -m 644 "$APP_DIR/deploy/sentinela-simulator.service" /etc/systemd/system/sentinela-simulator.service
systemctl daemon-reload
systemctl enable --quiet sentinela-api sentinela-simulator
systemctl restart sentinela-api
sleep 2
systemctl restart sentinela-simulator

echo "==> nginx"
install -m 644 "$APP_DIR/deploy/nginx-sentinela.conf" /etc/nginx/sites-available/sentinela-to
ln -sf /etc/nginx/sites-available/sentinela-to /etc/nginx/sites-enabled/sentinela-to
rm -f /etc/nginx/sites-enabled/default
nginx -t -q
systemctl enable --quiet nginx
systemctl reload nginx || systemctl restart nginx

if command -v ufw >/dev/null 2>&1 && ufw status | grep -q "Status: active"; then
  ufw allow 'Nginx HTTP' >/dev/null
fi

echo "==> Verificação"
for _ in $(seq 1 30); do
  if curl -fsS -o /dev/null http://127.0.0.1/login; then
    echo "OK: painel respondendo"
    exit 0
  fi
  sleep 2
done
echo "ERRO: o painel não respondeu. Logs:" >&2
journalctl -u sentinela-api -n 40 --no-pager >&2
exit 1
