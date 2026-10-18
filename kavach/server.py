"""Kavach live server: replays the held-out UPI stream through the engine and pushes
decisions to the dashboard over a WebSocket. Also exposes the scoring API.

    uvicorn kavach.server:app --host 0.0.0.0 --port 8000
"""
import asyncio
import time
from pathlib import Path

import pandas as pd
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from pydantic import BaseModel

from .engine import ART, Engine

STATIC = Path(__file__).resolve().parent / "static"
app = FastAPI(title="Kavach - real-time UPI scam & mule shield")
engine = Engine()
clients: set[WebSocket] = set()


class State:
    speed = 25.0          # transactions per second of replay
    paused = False
    sim_ts = 5 * 86400.0
    stats = dict(scored=0, hold=0, step_up=0, allow=0, fraud=0, caught=0, false_alarm=0,
                 protected=0.0, fraud_amount=0.0, calls=0, scam_calls=0)


S = State()


async def broadcast(msg):
    dead = []
    for ws in clients:
        try:
            await ws.send_json(msg)
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)


def account(res):
    st = S.stats
    st["scored"] += 1
    st[res["decision"].lower()] += 1
    victim_payment = res.get("label") == 1 and not str(res.get("kind")).startswith("mule")
    if res.get("label") == 1:
        st["fraud"] += 1
        if res["decision"] != "ALLOW":
            st["caught"] += 1
    if victim_payment:  # money leaving a victim (mule hops are the same money, don't double count)
        st["fraud_amount"] += res["amount"]
        if res["decision"] != "ALLOW":
            st["protected"] += res["amount"]
    # synthetic ground truth: X*/C* are mule accounts, so freezing their other payments is not a false alarm
    elif res.get("label") == 0 and res["decision"] == "HOLD" and res["sender"][0] not in "XC":
        st["false_alarm"] += 1


def snapshot():
    return {"type": "stats", "stats": S.stats, "latency": engine.latency_stats(), "sim_ts": S.sim_ts,
            "watchlist": len(engine.watchlist), "speed": S.speed, "paused": S.paused}


async def replay():
    tx = pd.read_pickle(ART / "replay_txns.pkl").to_dict("records")
    calls = pd.read_pickle(ART / "replay_calls.pkl").to_dict("records")
    ci, last_stats = 0, 0.0
    for t in tx:
        while S.paused:
            await asyncio.sleep(0.2)
        while ci < len(calls) and calls[ci]["ts"] <= t["ts"]:
            c = calls[ci]
            ci += 1
            r = engine.score_call(c["user"], c["ts"], c["transcript"])
            S.stats["calls"] += 1
            if r["scam"]:
                S.stats["scam_calls"] += 1
                await broadcast({"type": "call", **r, "transcript": c["transcript"]})
        S.sim_ts = t["ts"]
        res = engine.score(t)
        account(res)
        if res["decision"] != "ALLOW" or S.stats["scored"] % 5 == 0:
            await broadcast({"type": "txn", **res})
        if time.time() - last_stats > 0.5:
            last_stats = time.time()
            await broadcast(snapshot())
        await asyncio.sleep(1.0 / S.speed)
    await broadcast({"type": "done"})


@app.on_event("startup")
async def start():
    asyncio.create_task(replay())


@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"accelerator": engine.accel, "metrics": engine.metrics, "latency": engine.latency_stats(),
            "thresholds": {"step_up": engine.thr_step, "hold": engine.thr_hold}}


class CallIn(BaseModel):
    user: str
    transcript: str


class TxnIn(BaseModel):
    sender: str
    receiver: str
    amount: float
    device: str | None = None


class Control(BaseModel):
    speed: float | None = None
    paused: bool | None = None


@app.post("/api/call")
async def post_call(c: CallIn):
    r = engine.score_call(c.user, S.sim_ts, c.transcript)
    await broadcast({"type": "call", **r, "transcript": c.transcript, "manual": True})
    return r


@app.post("/api/txn")
async def post_txn(t: TxnIn):
    S.sim_ts += 1
    d = {k: v for k, v in t.model_dump().items() if v is not None}
    d.update(ts=S.sim_ts, txn_id=f"LIVE{int(time.time() * 1000) % 10**7}")
    res = engine.score(engine.enrich(d))
    await broadcast({"type": "txn", **res, "manual": True})
    return res


@app.post("/api/control")
def control(c: Control):
    if c.speed:
        S.speed = max(1.0, min(c.speed, 500.0))
    if c.paused is not None:
        S.paused = c.paused
    return snapshot()


@app.websocket("/ws")
async def ws(sock: WebSocket):
    await sock.accept()
    clients.add(sock)
    await sock.send_json({"type": "hello", **health()})
    try:
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        clients.discard(sock)
