# -*- coding: utf-8 -*-
"""Заливка изменённых файлов LotM на Aeza VPS (/root/lotm-bot).

Использование:
    python _deploy_push.py            # показать разницу
    python _deploy_push.py --push     # залить + рестарт сервиса

Файлы .env и data/ НЕ ТРОГАЮТСЯ (на сервере свой прод-конфиг и своя БД).
"""
import hashlib
import os
import sys

import paramiko

HOST, USER = "147.45.79.104", "root"
REMOTE_DIR = "/root/lotm-bot"
KEY = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Максим"), ".ssh", "id_ed25519")

# только эти файлы/маски; .env, data/, __pycache__ — запрещены
FILES = [
    "config.py", "db.py", "web_app.py", "main.py",
    "h_profile.py", "h_shop.py", "h_story.py",
    "combat_engine.py", "rules.py", "ui.py", "lotm_data.py",
    "requirements.txt", "static/index.html",
    "test_economy.py",
]
BLOCK = (".env", "data/", "__pycache__/", ".db")


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if os.path.exists(KEY):
        try:
            cli.connect(HOST, username=USER, key_filename=KEY, timeout=20,
                        look_for_keys=False, allow_agent=False)
            return cli
        except paramiko.ssh_exception.SSHException:
            pass
    pw = ""
    pf = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VPS_ROOT.txt")
    if os.path.exists(pf):
        pw = open(pf, encoding="utf-8").read().strip()
    cli.connect(HOST, username=USER, password=pw, timeout=20,
                look_for_keys=False, allow_agent=False)
    return cli


def md5(path):
    h = hashlib.md5()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def main():
    push = "--push" in sys.argv
    cli = connect()
    sftp = cli.open_sftp()

    changed, same = [], []
    for rel in FILES:
        if any(b in rel for b in BLOCK):
            sys.exit(f"[!] запрещённый файл в списке: {rel}")
        local = rel.replace("/", os.sep)
        if not os.path.exists(local):
            print(f"[skip] локально нет: {rel}")
            continue
        lh = md5(local)
        remote = f"{REMOTE_DIR}/{rel}"
        try:
            rh = md5(_tmp(sftp, remote))
        except IOError:
            rh = None
        (changed if lh != rh else same).append((rel, lh, rh, os.path.getsize(local)))

    print(f"[i] к заливке: {len(changed)}   без изменений: {len(same)}")
    for rel, lh, rh, sz in changed:
        print(f"  + {rel:24} {sz:>8} B   {str(rh)[:8]} -> {lh[:8]}")

    if not push:
        sftp.close(); cli.close()
        print("\n[!] сухой прогон. Для заливки: python _deploy_push.py --push")
        return

    stamp = __import__("datetime").datetime.now().strftime("%Y%m%d-%H%M%S")
    bak = f"/root/lotm-bot.bak-{stamp}"
    _run(cli, f"cp -a {REMOTE_DIR} {bak} && rm -rf {bak}/data && echo backup: {bak}")

    for rel, lh, rh, sz in changed:
        local = rel.replace("/", os.sep)
        # загрузка через временный файл + атомарная подмена
        tmp = f"/tmp/up_{os.path.basename(local)}"
        sftp.put(local, tmp)
        _run(cli, f"cp {tmp} {REMOTE_DIR}/{rel} && rm -f {tmp}")
        chk = _out(cli, f"md5sum {REMOTE_DIR}/{rel} | cut -c1-32").strip()
        print(f"  {'OK ' if chk == lh else 'FAIL'} {rel}  {chk[:8]}")

    sftp.close()
    print("\n[i] перезапуск lotm-bot ...")
    _run(cli, "systemctl restart lotm-bot && sleep 6 && systemctl is-active lotm-bot")
    _run(cli, "journalctl -u lotm-bot -n 25 --no-pager")
    _run(cli, "curl -s -o /dev/null -w 'local:8000 %{http_code}\\n' http://127.0.0.1:8000/")
    _run(cli, "curl -s -o /dev/null -w 'https %{http_code}\\n' https://creatureofnight.online/ "
              "|| echo 'https: проверь вручную'")
    cli.close()


def _tmp(sftp, remote):
    """Скачать удалённый файл во временный и вернуть локальный путь."""
    import tempfile
    fd, path = tempfile.mkstemp()
    os.close(fd)
    sftp.get(remote, path)
    return path


def _run(cli, cmd):
    print(f"\n$ {cmd}")
    _, out, _ = cli.exec_command(cmd, timeout=300, get_pty=True)
    for line in iter(out.readline, ""):
        sys.stdout.write(line)
    sys.stdout.flush()


def _out(cli, cmd):
    _, out, _ = cli.exec_command(cmd, timeout=60)
    return out.read().decode()


if __name__ == "__main__":
    main()
