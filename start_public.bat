@echo off
start "LOTM Bot" cmd /k "python main.py"
timeout /t 3 >nul
start "Cloudflare" cmd /k "cloudflared tunnel --url http://127.0.0.1:8000"
