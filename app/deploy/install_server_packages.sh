#!/usr/bin/env bash
set -euo pipefail

sudo apt update
sudo apt install -y \
  python3-venv python3-pip \
  nginx mysql-client fail2ban unattended-upgrades clamav clamav-daemon \
  certbot python3-certbot-nginx \
  libpango-1.0-0 libharfbuzz0b libpangoft2-1.0-0 libharfbuzz-subset0

# Dedicated unprivileged application account.
if ! id -u teriqen >/dev/null 2>&1; then
  sudo useradd --system --home /var/www/teriqen --shell /usr/sbin/nologin --gid www-data teriqen
fi
sudo systemctl enable --now clamav-daemon || true
sudo systemctl enable --now fail2ban || true
