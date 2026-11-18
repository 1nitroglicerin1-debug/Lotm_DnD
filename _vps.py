# -*- coding: utf-8 -*-
"""SSH-хелпер для Aeza VPS (147.45.79.104, root).

Использование:
    $env:VPS_PASS="..."; python _vps.py --cmd "uptime"
    $env:VPS_PASS="..."; python _vps.py --script _deploy_https.sh

При первом входе по паролю автоматически ставит локальный SSH-ключ
в authorized_keys — дальше пароль не нужен.
"""
import argparse
import os
import sys

import paramiko

HOST, USER = "147.45.79.104", "root"
KEY = os.path.join(os.environ.get("USERPROFILE", r"C:\Users\Максим"), ".ssh", "id_ed25519")
PUBKEY = "ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIJOruDlT6L7H8x5VRnZJUURasibRzvUmzVjF7yGb7Fj2"


def connect():
    cli = paramiko.SSHClient()
    cli.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    if os.path.exists(KEY):
        try:
            cli.connect(HOST, username=USER, key_filename=KEY, timeout=15,
                        look_for_keys=False, allow_agent=False)
            return cli, "key"
        except paramiko.ssh_exception.SSHException as e:
            print(f"[i] вход по ключу не удался: {e}")
    pw = os.environ.get("VPS_PASS", "").strip()
    if not pw:
        # root-пароль из панели Aeza лежит в VPS_ROOT.txt рядом со скриптом
        pw_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "VPS_ROOT.txt")
        if os.path.exists(pw_file):
            with open(pw_file, encoding="utf-8") as f:
                pw = f.read().strip()
    if not pw:
        sys.exit("[!] Нет доступа по ключу. Положи root-пароль в VPS_ROOT.txt или задай $env:VPS_PASS.")
    cli.connect(HOST, username=USER, password=pw, timeout=15,
                 look_for_keys=False, allow_agent=False)
    return cli, "password"


def run(cli, cmd, timeout=900):
    print(f"\n$ {cmd[:200]}")
    _, stdout, _ = cli.exec_command(cmd, timeout=timeout, get_pty=True)
    for line in iter(stdout.readline, ""):
        if not line:
            break
        sys.stdout.write(line)
        sys.stdout.flush()
    return stdout.channel.recv_exit_status()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cmd")
    ap.add_argument("--script")
    ap.add_argument("--timeout", type=int, default=900)
    a = ap.parse_args()
    if not (a.cmd or a.script):
        sys.exit("укажи --cmd или --script")
    cli, mode = connect()
    print(f"[i] SSH-подключение установлено ({mode})")
    if mode == "password":
        install = (
            "mkdir -p /root/.ssh && chmod 700 /root/.ssh && "
            "touch /root/.ssh/authorized_keys && chmod 600 /root/.ssh/authorized_keys && "
            f"grep -qF '{PUBKEY}' /root/.ssh/authorized_keys || echo '{PUBKEY}' >> /root/.ssh/authorized_keys"
        )
        run(cli, install)
        print("[i] SSH-ключ установлен на сервере (следующие запуски — без пароля).")
    try:
        if a.script:
            sftp = cli.open_sftp()
            remote = "/tmp/" + os.path.basename(a.script)
            sftp.put(a.script, remote)
            sftp.close()
            rc = run(cli, f"tr -d '\\r' < {remote} > {remote}.lf && bash {remote}.lf",
                     timeout=a.timeout)
        else:
            rc = run(cli, a.cmd, timeout=a.timeout)
    finally:
        cli.close()
    sys.exit(rc or 0)


if __name__ == "__main__":
    main()
