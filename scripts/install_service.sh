#!/usr/bin/env bash
#
# Install (or reinstall) the airable-api systemd service.
#
#   sudo ./scripts/install_service.sh
#
# Environment overrides:
#   APP_DIR       repo directory containing .venv (default: this script's parent dir)
#   SERVICE_USER  user the service runs as (default: the sudo caller, else current user)
#   RUN_DNS       1 to pass --no-dns=0 behaviour (default: honour config.yaml)
#
set -euo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
SERVICE_USER="${SERVICE_USER:-${SUDO_USER:-$(id -un)}}"
PYTHON="$APP_DIR/.venv/bin/python"
UNIT=/etc/systemd/system/airable-api.service

if [[ $EUID -ne 0 ]]; then
    echo "please run with sudo (needs to write $UNIT)" >&2
    exit 1
fi

if [[ ! -x "$PYTHON" ]]; then
    echo "python not found: $PYTHON" >&2
    echo "create the venv first, e.g. python3 -m venv .venv && .venv/bin/pip install -r requirements.txt" >&2
    exit 1
fi

if ! id "$SERVICE_USER" >/dev/null 2>&1; then
    echo "user does not exist: $SERVICE_USER" >&2
    exit 1
fi

echo "app dir      : $APP_DIR"
echo "service user : $SERVICE_USER"
echo "python       : $PYTHON"

cat > "$UNIT" <<UNIT
[Unit]
Description=airable-compatible local radio API
Documentation=file://$APP_DIR/README.md
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$SERVICE_USER
WorkingDirectory=$APP_DIR
ExecStart=$PYTHON -m app.proxy --config config.yaml
Environment=PYTHONUNBUFFERED=1
Restart=on-failure
RestartSec=5

# Allow binding to privileged ports (443/80) without running as root.
AmbientCapabilities=CAP_NET_BIND_SERVICE
CapabilityBoundingSet=CAP_NET_BIND_SERVICE

# Hardening (the app only needs to write inside APP_DIR/data and stations.yaml).
PrivateTmp=true
ProtectSystem=strict
ReadWritePaths=$APP_DIR

[Install]
WantedBy=multi-user.target
UNIT

systemctl daemon-reload
systemctl enable airable-api.service
systemctl restart airable-api.service

sleep 1
systemctl --no-pager --full status airable-api.service || true

echo
echo "done. follow the log with:"
echo "  journalctl -u airable-api.service -f"
