#!/bin/bash
# Привязать FastAPI к 127.0.0.1 — наружу только через nginx (HTTPS).
set -uo pipefail
ENV_FILE="/root/lotm-bot/.env"

echo "=== правка $ENV_FILE ==="
if grep -q "^WEB_HOST=" "$ENV_FILE"; then
  sed -i "s|^WEB_HOST=.*|WEB_HOST=127.0.0.1|" "$ENV_FILE"
else
  printf "WEB_HOST=127.0.0.1\n" >> "$ENV_FILE"
fi
grep -n "^WEB_HOST=\|^WEBAPP_URL=" "$ENV_FILE"

echo "=== рестарт lotm-bot ==="
systemctl restart lotm-bot
sleep 14

echo "--- локально 127.0.0.1:8000 (должен быть 200)"
curl -s -o /dev/null -w "code=%{http_code}\n" http://127.0.0.1:8000/
echo "--- снаружи :8000 (должен быть таймаут/отказ)"
curl -s -o /dev/null -w "code=%{http_code}\n" --connect-timeout 6 http://147.45.79.104:8000/ || echo "порт 8000 снаружи закрыт OK"
echo "--- HTTPS через nginx (должен быть 200)"
curl -s -o /dev/null -w "code=%{http_code}\n" https://147.45.79.104.sslip.io/
systemctl is-active lotm-bot
