"""
Cria um usuário do painel ou redefine a senha de um existente.

Uso (a partir da raiz do projeto):
    python -m scripts.create_user <usuario>
"""

from __future__ import annotations

import getpass
import sys

from backend.core.auth import DB_PATH, UserStore

MIN_PASSWORD_LENGTH = 8


def main() -> int:
    if len(sys.argv) != 2:
        print(__doc__.strip())
        return 1
    username = sys.argv[1].strip()
    password = getpass.getpass(f"Nova senha para '{username}': ")
    if len(password) < MIN_PASSWORD_LENGTH:
        print(f"A senha precisa ter pelo menos {MIN_PASSWORD_LENGTH} caracteres.")
        return 1
    if getpass.getpass("Confirme a senha: ") != password:
        print("As senhas não conferem.")
        return 1
    UserStore(DB_PATH).set_password(username, password)
    print(f"Usuário '{username}' salvo em {DB_PATH}. Sessões anteriores foram encerradas.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
