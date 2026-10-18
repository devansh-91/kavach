"""Run: python -m pytest -q   (needs artifacts from `python -m kavach.train`)"""
import numpy as np
import pytest
from fastapi.testclient import TestClient

from kavach.engine import Engine
from kavach.features import FEATURES, FeatureStore

CALL = "main CBI officer bol raha hoon, aap digital arrest mein ho, video call band mat karna, paise transfer karo"


@pytest.fixture(scope="module")
def engine():
    return Engine()


def txn(s, r, amt, ts, **kw):
    return dict(sender=s, receiver=r, amount=amt, ts=ts, device=f"D{s}_0", sender_age=500,
                receiver_age=500, receiver_type=r[0], **kw)


def test_features_only_use_the_past():
    fs = FeatureStore()
    t = txn("U1", "U2", 1000, 100.0)
    before = fs.features(t)
    fs.observe(t)
    assert fs.features(t) != before          # state changed only after observe
    assert before[FEATURES.index("new_payee")] == 1.0
    assert fs.features(t)[FEATURES.index("new_payee")] == 0.0


def test_mule_pass_through_is_visible():
    fs = FeatureStore()
    for i, v in enumerate(["U1", "U2", "U3"]):
        fs.observe(txn(v, "X1", 20000, 100.0 + i))
    f = dict(zip(FEATURES, fs.features(txn("X1", "C1", 55000, 400.0))))
    assert f["sender_passthrough_1h"] > 0.8
    assert f["sender_mins_since_in"] < np.log1p(10)


def test_scam_call_raises_risk(engine):
    ts = 5 * 86400 + 12 * 3600
    calm = engine.score(engine.enrich({"sender": "U00101", "receiver": "U02020", "amount": 15000, "ts": ts}))
    engine.score_call("U00102", ts, CALL)
    scam = engine.score(engine.enrich({"sender": "U00102", "receiver": "U02021", "amount": 15000, "ts": ts + 60}))
    assert calm["decision"] == "ALLOW"
    assert scam["decision"] in ("STEP_UP", "HOLD") and scam["risk"] > calm["risk"]
    assert scam["reasons"]


def test_short_or_empty_call_is_not_scam(engine):
    assert engine.score_call("U00103", 0, "hello")["risk"] == 0.0


def test_api_validation():
    from kavach.server import app
    with TestClient(app) as c:
        assert c.post("/api/txn", json={"sender": "U1", "receiver": "U1", "amount": 10}).status_code == 422
        assert c.post("/api/txn", json={"sender": "U1", "receiver": "U2", "amount": -1}).status_code == 422
        assert c.post("/api/txn", json={"sender": "", "receiver": "U2", "amount": 5}).status_code == 422
        r = c.post("/api/txn", json={"sender": "U00001", "receiver": "M0001", "amount": 250})
        assert r.status_code == 200 and r.json()["decision"] in ("ALLOW", "STEP_UP", "HOLD")
        assert c.get("/api/health").json()["accelerator"]["tree_format"] in ("compress_trees", "zdnn_tensors")


# ---- security -----------------------------------------------------------------
def _client(monkeypatch, key=""):
    import importlib
    import kavach.server as srv
    monkeypatch.setenv("KAVACH_API_KEY", key)
    return TestClient(importlib.reload(srv).app)


def test_api_key_required_when_configured(monkeypatch):
    with _client(monkeypatch, "s3cret") as c:
        body = {"sender": "U00001", "receiver": "M0001", "amount": 250}
        assert c.post("/api/txn", json=body).status_code == 401
        assert c.post("/api/txn", json=body, headers={"X-API-Key": "wrong"}).status_code == 401
        assert c.post("/api/control", json={"paused": True}).status_code == 401
        assert c.post("/api/txn", json=body, headers={"X-API-Key": "s3cret"}).status_code == 200
        with c.websocket_connect("/ws") as ws:   # wrong key -> closed with policy violation
            ws.send_text('{"key": "nope"}')
            with pytest.raises(Exception):
                ws.receive_json()
        with c.websocket_connect("/ws") as ws:
            ws.send_text('{"key": "s3cret"}')
            assert ws.receive_json()["type"] == "hello"


def test_rejects_malformed_and_hostile_input(monkeypatch):
    with _client(monkeypatch) as c:
        bad = [
            {"sender": "U1", "receiver": "U2", "amount": "NaN"},
            {"sender": "U1", "receiver": "U2", "amount": "Infinity"},
            {"sender": "U1", "receiver": "U2", "amount": 1e9},
            {"sender": "<script>", "receiver": "U2", "amount": 5},
            {"sender": "U1", "receiver": "U2", "amount": 5, "is_admin": True},
            {"sender": "U1" * 40, "receiver": "U2", "amount": 5},
        ]
        for b in bad:
            assert c.post("/api/txn", json=b).status_code == 422, b
        assert c.post("/api/control", json={"speed": 1e9}).status_code == 422


def test_security_headers_and_no_docs(monkeypatch):
    with _client(monkeypatch) as c:
        r = c.get("/")
        assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
        assert "unsafe-inline" not in r.headers["content-security-policy"].split("script-src")[1].split(";")[0]
        assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"
        assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
        assert c.get("/api/health").headers["cache-control"] == "no-store"
        pre = c.options("/api/txn", headers={"Origin": "https://evil.example", "Access-Control-Request-Method": "POST"})
        assert "access-control-allow-origin" not in pre.headers   # no CORS -> other sites can't call the API


def test_rate_limit(monkeypatch):
    with _client(monkeypatch) as c:
        codes = [c.get("/api/health").status_code for _ in range(80)]
        assert 429 in codes


def test_fail_safe_never_allows_on_error(monkeypatch):
    import kavach.server as srv
    with _client(monkeypatch) as c:
        import kavach.server as srv
        monkeypatch.setattr(srv.engine, "score", lambda t: 1 / 0)
        r = c.post("/api/txn", json={"sender": "U00001", "receiver": "U00002", "amount": 900})
        assert r.status_code == 200 and r.json()["decision"] == "STEP_UP"


def test_tampered_model_is_refused(tmp_path, monkeypatch):
    import kavach.engine as eng
    bad = tmp_path / "m.pmml"
    bad.write_text(eng.MODEL.read_text().replace("<Segment", "<Segment ", 1))
    monkeypatch.setattr(eng, "MODEL", bad)
    with pytest.raises(RuntimeError, match="integrity"):
        eng.Engine(warmup=False)


# ---- abuse / robustness ---------------------------------------------------------
def test_framing_attack_cannot_freeze_an_innocent_account(engine):
    ts = 5 * 86400 + 2 * 3600
    for i, attacker in enumerate(["X071", "X072", "X073"]):  # risky money pushed into a victim's account
        engine.score(engine.enrich({"sender": attacker, "receiver": "U04001", "amount": 60000,
                                    "ts": ts + i * 60, "device": f"BAD{i}"}))
    own = engine.score(engine.enrich({"sender": "U04001", "receiver": "M0003", "amount": 800, "ts": ts + 900}))
    assert own["decision"] != "HOLD"          # at most an OTP, never a freeze


def test_new_device_drain_is_stepped_up(engine):
    r = engine.score(engine.enrich({"sender": "U00203", "receiver": "U03003", "amount": 25000,
                                    "ts": 5 * 86400 + 3 * 3600, "device": "EVILPHONE"}))
    assert r["decision"] in ("STEP_UP", "HOLD")


def test_ordinary_payments_flow(engine):
    ts = 5 * 86400 + 11 * 3600
    rs = [engine.score(engine.enrich({"sender": f"U00{310 + i}", "receiver": "M0011", "amount": 350, "ts": ts + i}))
          for i in range(20)]
    assert all(r["decision"] == "ALLOW" for r in rs)
