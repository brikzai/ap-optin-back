import jwt as pyjwt

from scripts.gerar_chaves_jwt import gerar_par
from scripts.gerar_jwt import gerar_token


def test_token_gerado_valida_com_a_publica_e_carrega_financiador(tmp_path):
    priv, pub = gerar_par(tmp_path)
    token = gerar_token(priv, financiador_id="12345678000199", horas=1)
    claims = pyjwt.decode(token, pub.read_text(), algorithms=["RS256"], issuer="brikz-iam")
    assert claims["financiador_id"] == "12345678000199"
    assert claims["sub"] == "dev-user"
