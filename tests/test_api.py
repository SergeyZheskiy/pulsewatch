import pytest

NEW = {"name": "Website", "url": "https://ok.test/", "interval_seconds": 30}


def create(client, **overrides) -> dict:
    r = client.post("/api/monitors", json={**NEW, **overrides})
    assert r.status_code == 201, r.text
    return r.json()


def test_healthz(client):
    assert client.get("/healthz").json()["status"] == "ok"


def test_write_api_requires_key(client):
    assert client.get("/api/monitors", headers={"X-API-Key": "wrong"}).status_code == 401
    client.headers.pop("X-API-Key")
    assert client.post("/api/monitors", json=NEW).status_code == 401
    assert client.get("/api/status").status_code == 200  # public


def test_crud(client):
    m = create(client)
    assert m["status"] == "unknown" and m["url"] == "https://ok.test/"
    assert m["created_at"].endswith("Z")

    r = client.patch(f"/api/monitors/{m['id']}", json={"name": "Homepage", "keyword": "hello"})
    assert r.status_code == 200 and r.json()["name"] == "Homepage" and r.json()["keyword"] == "hello"
    assert client.patch(f"/api/monitors/{m['id']}", json={"keyword": ""}).json()["keyword"] is None

    assert [x["id"] for x in client.get("/api/monitors").json()] == [m["id"]]
    assert client.delete(f"/api/monitors/{m['id']}").status_code == 204
    assert client.get(f"/api/monitors/{m['id']}").status_code == 404


@pytest.mark.parametrize(
    "payload",
    [
        {"interval_seconds": 5},
        {"url": "not-a-url"},
        {"url": "ftp://ok.test/"},
        {"method": "HEAD", "keyword": "x"},
        {"timeout_seconds": 0},
        {"url": "http://127.0.0.1:8000/"},
        {"url": "http://169.254.169.254/"},
    ],
)
def test_validation(client, payload):
    assert client.post("/api/monitors", json={**NEW, **payload}).status_code == 422


def test_check_now(client):
    m = create(client)
    r = client.post(f"/api/monitors/{m['id']}/check")
    assert r.status_code == 200
    assert r.json()["ok"] is True and r.json()["status_code"] == 200
    assert client.get(f"/api/monitors/{m['id']}").json()["status"] == "up"


def test_scheduler_only_checks_due_and_active_monitors(client, tick):
    create(client, name="A")
    create(client, name="B", is_active=False)
    assert tick() == 1
    assert tick() == 0  # A was just checked, interval not elapsed


def test_outage_lifecycle_alerts_and_incidents(client, tick, net):
    m = create(client, name="Flappy", url="https://flappy.test/")
    check = lambda: client.post(f"/api/monitors/{m['id']}/check")  # noqa: E731

    check()  # unknown -> up: no alert on startup
    assert net.sent == []

    net.flappy_status = 502
    check()  # one failure is tolerated (failure_threshold=2)
    assert client.get(f"/api/monitors/{m['id']}").json()["status"] == "up"
    check()
    assert client.get(f"/api/monitors/{m['id']}").json()["status"] == "down"
    assert {host for host, _ in net.sent} == {"hooks.test", "api.telegram.org"}
    webhook = next(body for host, body in net.sent if host == "hooks.test")
    assert webhook["event"] == "monitor.down" and webhook["error"] == "Unexpected status 502"
    assert webhook["text"] == webhook["content"]  # Slack + Discord compatible

    incidents = client.get(f"/api/monitors/{m['id']}/incidents").json()
    assert len(incidents) == 1 and incidents[0]["resolved_at"] is None

    net.sent.clear()
    net.flappy_status = 200
    check()
    assert "back UP" in net.sent[0][1]["text"]
    assert client.get(f"/api/monitors/{m['id']}/incidents").json()[0]["resolved_at"] is not None

    stats = client.get(f"/api/monitors/{m['id']}/stats").json()
    assert stats["checks"] == 4 and stats["uptime_pct"] == 50.0
    checks = client.get(f"/api/monitors/{m['id']}/checks?limit=2").json()
    assert [c["ok"] for c in checks] == [True, False]


def test_status_page_and_badge(client):
    up = create(client, name="Public API")
    down = create(client, name="Legacy app", url="https://down.test/")
    hidden = create(client, name="Internal thing", is_public=False)
    for m in (up, down, down):
        client.post(f"/api/monitors/{m['id']}/check")

    page = client.get("/")
    assert page.status_code == 200
    assert "Public API" in page.text and "Legacy app" in page.text
    assert "Internal thing" not in page.text
    assert "Partial outage" in page.text
    assert "Unexpected status 503" in page.text  # incident cause

    public = client.get("/api/status").json()
    assert [(p["name"], p["status"]) for p in public] == [("Public API", "up"), ("Legacy app", "down")]

    badge = client.get(f"/badge/{up['id']}.svg")
    assert badge.headers["content-type"] == "image/svg+xml"
    assert "100%" in badge.text and "#4c1" in badge.text
    assert "0%" in client.get(f"/badge/{down['id']}.svg").text
    assert client.get(f"/badge/{hidden['id']}.svg").status_code == 404


def test_deleting_a_monitor_removes_its_history(client):
    m = create(client)
    client.post(f"/api/monitors/{m['id']}/check")
    client.delete(f"/api/monitors/{m['id']}")
    assert client.get(f"/api/monitors/{m['id']}/checks").status_code == 404
