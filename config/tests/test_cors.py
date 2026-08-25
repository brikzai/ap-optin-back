def test_cors_permite_origem_configurada(client):
    response = client.get("/api/v1/health", HTTP_ORIGIN="http://localhost:5173")
    assert response["Access-Control-Allow-Origin"] == "http://localhost:5173"


def test_cors_nao_libera_origem_nao_configurada(client):
    response = client.get("/api/v1/health", HTTP_ORIGIN="http://nao-autorizado.example.com")
    assert "Access-Control-Allow-Origin" not in response
