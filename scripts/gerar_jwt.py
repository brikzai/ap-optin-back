"""Emite um JWT RS256 aceito por shared/jwt_auth.py (iss=brikz-iam, claim financiador_id).

    python scripts/gerar_jwt.py --chave keys/homolog/jwt_private.pem --financiador 12345678000199 --horas 24
"""
import argparse
import time
from pathlib import Path

import jwt as pyjwt


def gerar_token(chave_privada: Path, financiador_id: str, horas: int = 24, sub: str = "dev-user") -> str:
    agora = int(time.time())
    return pyjwt.encode(
        {"iss": "brikz-iam", "sub": sub, "iat": agora, "exp": agora + horas * 3600, "financiador_id": financiador_id},
        Path(chave_privada).read_text(),
        algorithm="RS256",
    )


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--chave", required=True)
    p.add_argument("--financiador", required=True)
    p.add_argument("--horas", type=int, default=24)
    p.add_argument("--sub", default="dev-user")
    a = p.parse_args()
    print(gerar_token(Path(a.chave), a.financiador, a.horas, a.sub))
