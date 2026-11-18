#!/bin/bash
# Бесплатный домен (sslip.io) + HTTPS (Let's Encrypt) для Mini App LotM.
# Запускать на Aeza VPS от root. FastAPI остаётся на :8000, nginx проксирует 80/443.
set -uo pipefail

# Домен передаётся аргументом: bash _deploy_https.sh play.example.ru
# Без аргумента — бесплатный sslip.io (IP-поддомен, указывает на нас сам).
DOMAIN="${1:-147.45.79.104.sslip.io}"
ENV_FILE="/root/lotm-bot/.env"
LOG(){ echo -e "\n=== $1 ==="; }

LOG "0/7 подготовка ($DOMAIN)"
DOM_IP=$(getent ahostsv4 "$DOMAIN" 2>/dev/null | awk '{print $1; exit}')
if [ "$DOM_IP" != "147.45.79.104" ]; then
  echo "[!] $DOMAIN сейчас резолвится в '${DOM_IP:-пусто}', а нужен 147.45.79.104."
  echo "    A-запись ещё не доехала (DNS обновляется минуты-часы) — запусти скрипт позже."
  exit 1
fi
echo "DNS: $DOMAIN -> 147.45.79.104 OK"
if ss -tlnp | grep -qE ':(80|443) ' && ! ss -tlnp | grep -q nginx; then
  echo "[!] Порт 80/443 занят НЕ nginx — останавливаюсь. Что там:"; ss -tlnp | grep -E ':(80|443) '
  exit 1
fi
echo "порты 80/443 свободны (или заняты nginx) — ок"
free -m | head -2; df -h / | tail -1

LOG "1/7 установка nginx + certbot"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq nginx certbot python3-certbot-nginx >/tmp/apt.log 2>&1
RC=$?
tail -3 /tmp/apt.log
[ $RC -ne 0 ] && { echo "[!] apt не удался"; exit 1; }

LOG "2/7 nginx-конфиг: $DOMAIN -> 127.0.0.1:8000"
cat > /etc/nginx/sites-available/lotm <<EOF
server {
    listen 80;
    server_name $DOMAIN;
    client_max_body_size 10m;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_read_timeout 300s;
        proxy_send_timeout 300s;
    }
}
EOF
ln -sf /etc/nginx/sites-available/lotm /etc/nginx/sites-enabled/lotm
rm -f /etc/nginx/sites-enabled/default
nginx -t 2>&1 || exit 1
systemctl enable --now nginx >/dev/null 2>&1
systemctl reload nginx && echo "nginx перезапущен"

LOG "3/7 фаервол (ufw)"
if ufw status 2>/dev/null | grep -q "Status: active"; then
  ufw allow 80/tcp >/dev/null; ufw allow 443/tcp >/dev/null
  echo "ufw: открыты 80 и 443"
else
  echo "ufw не активен — порты открыты на уровне Aeza"
fi

LOG "4/7 сертификат Let's Encrypt (бесплатно, автопродление)"
certbot --nginx -d "$DOMAIN" --non-interactive --agree-tos \
  --register-unsafely-without-email --redirect >/tmp/certbot.log 2>&1
RC=$?
tail -12 /tmp/certbot.log
[ $RC -ne 0 ] && { echo "[!] certbot не удался (код $RC)"; exit 1; }

LOG "5/7 WEBAPP_URL в $ENV_FILE"
[ -f "$ENV_FILE" ] || { echo "[!] $ENV_FILE не найден"; exit 1; }
if grep -q "^WEBAPP_URL=" "$ENV_FILE"; then
  sed -i "s|^WEBAPP_URL=.*|WEBAPP_URL=https://$DOMAIN|" "$ENV_FILE"
else
  printf "\n# Публичный URL Mini App (домен + HTTPS)\nWEBAPP_URL=https://$DOMAIN\n" >> "$ENV_FILE"
fi
grep -n "^WEBAPP_URL=" "$ENV_FILE"

LOG "6/7 перезапуск lotm-bot (dnd-bot не трогаем)"
systemctl restart lotm-bot
sleep 4
systemctl is-active lotm-bot
journalctl -u lotm-bot -n 4 --no-pager | tail -4

LOG "7/7 проверки"
echo "--- FastAPI локально :8000"
curl -s -o /dev/null -w "http_code=%{http_code}\n" http://127.0.0.1:8000/
echo "--- https://$DOMAIN"
curl -s -o /dev/null -w "https_code=%{http_code} cert_verify=%{ssl_verify_result}\n" "https://$DOMAIN/"
echo "--- сертификат"
echo | openssl s_client -servername "$DOMAIN" -connect "$DOMAIN":443 2>/dev/null \
  | openssl x509 -noout -subject -issuer -dates
echo "--- таймер автопродления"
systemctl list-timers certbot.timer --no-pager | head -3

LOG "бонус: кнопка меню Telegram (Web App в левом нижнем углу)"
TOKEN=$(grep -m1 '^BOT_TOKEN=' "$ENV_FILE" | cut -d= -f2)
curl -s "https://api.telegram.org/bot$TOKEN/setChatMenuButton" \
  -H "Content-Type: application/json" \
  -d "{\"menu_button\":{\"type\":\"web_app\",\"text\":\"Веб-Терминал\",\"web_app\":{\"url\":\"https://$DOMAIN\"}}}"
echo ""

echo ""
echo "DEPLOY_OK https://$DOMAIN"
