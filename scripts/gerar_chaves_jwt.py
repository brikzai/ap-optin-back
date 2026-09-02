"""Gera o par RSA usado para assinar JWTs de acesso à API (IdP de homolog/dev).

    python scripts/gerar_chaves_jwt.py keys/homolog

Escreve jwt_private.pem (fica SÓ na máquina de quem emite tokens) e jwt_public.pem
(vai para o segredo IAM_JWT_PUBLIC_KEY). Ambos gitignorados (*.pem).
"""
import sys
from pathlib import Path

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


def gerar_par(diretorio: Path):
    diretorio = Path(diretorio)
    diretorio.mkdir(parents=True, exist_ok=True)
    chave = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    priv = diretorio / "jwt_private.pem"
    pub = diretorio / "jwt_public.pem"
    priv.write_bytes(chave.private_bytes(
        serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption(),
    ))
    pub.write_bytes(chave.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    ))
    return priv, pub


if __name__ == "__main__":
    if len(sys.argv) != 2:
        sys.exit("uso: python scripts/gerar_chaves_jwt.py <diretorio>")
    priv, pub = gerar_par(Path(sys.argv[1]))
    print(f"privada: {priv}\npublica: {pub}")
