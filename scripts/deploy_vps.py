"""
Publica o Sentinela TO numa VPS Debian/Ubuntu via SSH (nginx + systemd).

    python -m scripts.deploy_vps root@IP

Na primeira execução pede a senha do servidor e instala uma chave SSH dedicada
(~/.ssh/sentinela_deploy); as próximas publicações usam só a chave.
A senha também pode vir da variável SENTINELA_VPS_PASSWORD.
Requer: pip install paramiko
"""

from __future__ import annotations

import argparse
import getpass
import io
import os
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

import paramiko

ROOT = Path(__file__).resolve().parents[1]
APP_DIR = "/opt/sentinela-to"
KEY_PATH = Path.home() / ".ssh" / "sentinela_deploy"
INCLUDE = ["backend", "frontend", "simulator", "scripts", "devkit", "deploy", "requirements.txt", "README.md"]
SKIP_DIRS = {"__pycache__", ".pytest_cache", ".venv", "data"}
LF_SUFFIXES = {".sh", ".service", ".conf"}


def load_or_create_key() -> paramiko.PKey:
    if KEY_PATH.exists():
        return paramiko.RSAKey.from_private_key_file(str(KEY_PATH))
    KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
    key = paramiko.RSAKey.generate(4096)
    key.write_private_key_file(str(KEY_PATH))
    KEY_PATH.with_suffix(".pub").write_text(f"{key.get_name()} {key.get_base64()} sentinela-deploy\n", encoding="utf-8")
    return key


def connect(host: str, user: str, key: paramiko.PKey) -> paramiko.SSHClient:
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(host, username=user, pkey=key, timeout=20, allow_agent=False, look_for_keys=False)
        print("Conectado com a chave de deploy.")
        return client
    except paramiko.AuthenticationException:
        pass
    password = os.environ.get("SENTINELA_VPS_PASSWORD") or getpass.getpass(f"Senha de {user}@{host}: ")
    client.connect(host, username=user, password=password, timeout=20, allow_agent=False, look_for_keys=False)
    pub = f"{key.get_name()} {key.get_base64()} sentinela-deploy"
    run(
        client,
        "umask 077; mkdir -p ~/.ssh; touch ~/.ssh/authorized_keys; "
        f"grep -qF '{key.get_base64()}' ~/.ssh/authorized_keys || echo '{pub}' >> ~/.ssh/authorized_keys",
    )
    print("Conectado com senha; chave de deploy instalada para as próximas publicações.")
    return client


def run(client: paramiko.SSHClient, command: str, stream: bool = False) -> str:
    _, stdout, stderr = client.exec_command(command, get_pty=stream)
    output = []
    for line in iter(stdout.readline, ""):
        output.append(line)
        if stream:
            sys.stdout.write(line)
            sys.stdout.flush()
    status = stdout.channel.recv_exit_status()
    if status != 0:
        raise RuntimeError(f"Falhou ({status}): {command}\n{''.join(output)}{stderr.read().decode(errors='replace')}")
    return "".join(output)


def build_archive() -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        for name in INCLUDE:
            base = ROOT / name
            paths = [base] if base.is_file() else [p for p in base.rglob("*") if p.is_file()]
            for path in paths:
                rel = path.relative_to(ROOT)
                if SKIP_DIRS.intersection(rel.parts) or path.suffix == ".pyc":
                    continue
                data = path.read_bytes()
                if path.suffix in LF_SUFFIXES:
                    data = data.replace(b"\r\n", b"\n")
                info = tarfile.TarInfo(rel.as_posix())
                info.size = len(data)
                info.mode = 0o755 if path.suffix == ".sh" else 0o644
                tar.addfile(info, io.BytesIO(data))
    return buf.getvalue()


def database_snapshot() -> Path | None:
    """Cópia consistente do banco local (usuários, histórico e curadoria), mesmo com o backend rodando."""
    source = ROOT / "data" / "sentinela.db"
    if not source.exists():
        return None
    target = Path(tempfile.mkdtemp()) / "sentinela.db"
    with sqlite3.connect(source) as src, sqlite3.connect(target) as dst:
        src.backup(dst)
    return target


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("target", help="usuario@ip da VPS")
    parser.add_argument("--no-data", action="store_true", help="não envia o banco local na primeira publicação")
    args = parser.parse_args()
    user, _, host = args.target.rpartition("@")
    user = user or "root"

    client = connect(host, user, load_or_create_key())
    sftp = client.open_sftp()

    print("Enviando o projeto...")
    sftp.putfo(io.BytesIO(build_archive()), "/tmp/sentinela-to.tar.gz")
    run(
        client,
        f"mkdir -p {APP_DIR}/data && cd {APP_DIR} && "
        "rm -rf backend frontend simulator scripts devkit deploy && "
        "tar -xzf /tmp/sentinela-to.tar.gz && rm /tmp/sentinela-to.tar.gz",
    )

    first_deploy = run(client, f"test -f {APP_DIR}/data/sentinela.db && echo sim || echo nao").strip() == "nao"
    sent_db = False
    if first_deploy and not args.no_data:
        snapshot = database_snapshot()
        if snapshot:
            print("Enviando o banco local (usuários, histórico e curadoria)...")
            sftp.put(str(snapshot), f"{APP_DIR}/data/sentinela.db")
            sent_db = True
    sftp.close()

    print("Provisionando o servidor (pode levar alguns minutos na primeira vez)...")
    run(client, f"sudo bash {APP_DIR}/deploy/setup.sh" if user != "root" else f"bash {APP_DIR}/deploy/setup.sh", stream=True)

    print(f"\nSentinela TO publicado: http://{host}/")
    if first_deploy and not sent_db:
        print(run(client, "grep -E '^SENTINELA_ADMIN_(USER|PASSWORD)=' /etc/sentinela-to.env").strip())
    elif sent_db:
        print("Use os mesmos usuários e senhas do ambiente local.")
    client.close()


if __name__ == "__main__":
    main()
