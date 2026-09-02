import importlib

import config.settings as settings_module


def _recarrega(monkeypatch, ambiente: str, cors: str = ""):
    monkeypatch.setenv("ENVIRONMENT", ambiente)
    monkeypatch.setenv("CORS_ALLOWED_ORIGINS", cors)
    return importlib.reload(settings_module)


def test_development_e_debug_com_cors_de_localhost(monkeypatch):
    s = _recarrega(monkeypatch, "development")
    assert s.DEBUG is True
    assert s.CORS_ALLOWED_ORIGIN_REGEXES  # localhost liberado
    assert not getattr(s, "CORS_ALLOWED_ORIGINS", None)


def test_homolog_nao_e_debug_e_usa_lista_explicita(monkeypatch):
    s = _recarrega(monkeypatch, "homolog", "https://a.example,https://b.example")
    assert s.DEBUG is False
    assert s.CORS_ALLOWED_ORIGINS == ["https://a.example", "https://b.example"]
    assert not getattr(s, "CORS_ALLOWED_ORIGIN_REGEXES", None)


def test_production_nao_e_debug(monkeypatch):
    assert _recarrega(monkeypatch, "production").DEBUG is False


def teardown_module(module):
    importlib.reload(settings_module)  # devolve o módulo ao estado do .env para os outros testes
