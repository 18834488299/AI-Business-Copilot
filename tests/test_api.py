from fastapi.testclient import TestClient

from api.main import app


client = TestClient(app)


def test_health_and_examples():
    health = client.get("/health")
    assert health.status_code == 200
    assert health.json()["status"] == "ok"

    examples = client.get("/api/examples")
    assert examples.status_code == 200
    assert len(examples.json()["examples"]) == 5
    assert "NovaScan" in examples.json()["examples"][0]


def test_local_chat_contract():
    response = client.post(
        "/api/chat",
        json={"message": "华东为什么下降？", "mode": "local", "session_id": "api-test"},
    )
    assert response.status_code == 200
    payload = response.json()
    assert payload["route"] == "region_diagnosis"
    assert payload["grounded"] is True
    assert {item["name"] for item in payload["tool_calls"]} >= {
        "query_sales",
        "query_feedback",
    }
    assert payload["sources"]
    assert payload["trace"]
