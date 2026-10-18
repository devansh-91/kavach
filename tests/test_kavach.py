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
