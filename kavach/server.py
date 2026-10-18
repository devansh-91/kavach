"""Kavach live server: replays the held-out UPI stream through the engine and pushes
decisions to the dashboard over a WebSocket. Also exposes the scoring API.

    uvicorn kavach.server:app --host 127.0.0.1 --port 8000

Security settings (environment variables):
    KAVACH_API_KEY   if set, every POST /api/* needs header X-API-Key and the dashboard
                     WebSocket must authenticate; open the dashboard as /#key=<key>
    KAVACH_DOCS=1    expose /docs and /openapi.json (off by default)
    KAVACH_RATE      max API requests per second per client IP (default 20)
"""
import asyncio
import hmac
import json
import logging
import os
import time
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, Header, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator

from .engine import ART, Engine, read_csv

STATIC = Path(__file__).resolve().parent / "static"
API_KEY = os.environ.get("KAVACH_API_KEY", "")
RATE = float(os.environ.get("KAVACH_RATE", "20"))
MAX_WS = 50
AUDIT = ART / "audit.jsonl"
log = logging.getLogger("kavach")

CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src https://fonts.gstatic.com; "
       "img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
HEADERS = {"Content-Security-Policy": CSP, "X-Frame-Options": "DENY", "X-Content-Type-Options": "nosniff",
           "Referrer-Policy": "no-referrer", "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
           "Cross-Origin-Opener-Policy": "same-origin"}


@asynccontextmanager
async def lifespan(_):
    task = asyncio.create_task(replay())
    yield
    task.cancel()


docs = os.environ.get("KAVACH_DOCS") == "1"
app = FastAPI(title="Kavach - real-time UPI scam & mule shield", lifespan=lifespan,
              docs_url="/docs" if docs else None, redoc_url=None, openapi_url="/openapi.json" if docs else None)
app.mount("/static", StaticFiles(directory=STATIC), name="static")
engine = Engine()
clients: set[WebSocket] = set()


class State:
    speed = 25.0          # transactions per second of replay
    paused = False
    sim_ts = 5 * 86400.0
    stats = dict(scored=0, hold=0, step_up=0, allow=0, fraud=0, caught=0, false_alarm=0,
                 protected=0.0, fraud_amount=0.0, calls=0, scam_calls=0, errors=0)


S = State()


# ---- security plumbing ------------------------------------------------------
_buckets = defaultdict(lambda: [RATE * 2, time.monotonic()])   # ip -> [tokens, last refill]


@app.middleware("http")
async def guard(request: Request, call_next):
    if request.url.path.startswith("/api/"):
        ip = request.client.host if request.client else "?"
        if len(_buckets) > 10000:
            _buckets.clear()
        b = _buckets[ip]
        now = time.monotonic()
        b[0] = min(RATE * 2, b[0] + (now - b[1]) * RATE)
        b[1] = now
        if b[0] < 1:
            return JSONResponse({"detail": "rate limit exceeded"}, status_code=429, headers=HEADERS)
        b[0] -= 1
    resp = await call_next(request)
    resp.headers.update(HEADERS)
    if request.url.path.startswith("/api/"):
        resp.headers["Cache-Control"] = "no-store"
    return resp


def key_ok(key: str | None) -> bool:
    return not API_KEY or (key is not None and hmac.compare_digest(key.encode(), API_KEY.encode()))


def require_key(x_api_key: str | None = Header(default=None)):
    if not key_ok(x_api_key):
        raise HTTPException(status_code=401, detail="invalid or missing API key")


def audit(res, source):
    """Append-only decision log: what was decided, why, by which model version."""
    rec = {"at": datetime.now(timezone.utc).isoformat(), "source": source, "model_sha256": engine.model_sha256[:16],
           **{k: res.get(k) for k in ("txn_id", "sender", "receiver", "amount", "risk", "decision", "latency_ms")},
           "reasons": [r["label"] for r in res.get("reasons", [])]}
    fd = os.open(AUDIT, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)  # owner-only, append-only
    with os.fdopen(fd, "a") as f:
        f.write(json.dumps(rec) + "\n")


def safe_score(t):
    """Fail safe, not open: if scoring breaks, the payment is stepped up, never silently allowed."""
    try:
        return engine.score(t)
    except Exception:
        log.exception("scoring failed for %s", t.get("txn_id"))
        S.stats["errors"] += 1
        return {"txn_id": t.get("txn_id"), "ts": t.get("ts"), "sender": t.get("sender"), "receiver": t.get("receiver"),
                "amount": t.get("amount"), "risk": None, "decision": "STEP_UP", "latency_ms": None,
                "reasons": [{"feature": "policy", "label": "Risk engine unavailable - fail-safe step-up", "impact": None}],
                "new_watchlist": None, "label": t.get("label"), "kind": t.get("kind")}


# ---- live replay --------------------------------------------------------------
async def broadcast(msg):
    dead = []
    for ws in list(clients):
        try:
            await asyncio.wait_for(ws.send_json(msg), timeout=2)
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
    tx = read_csv(ART / "replay_txns.csv.gz").to_dict("records")
    calls = read_csv(ART / "replay_calls.csv.gz").to_dict("records")
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
        res = safe_score(t)
        account(res)
        if res["decision"] != "ALLOW":
            audit(res, "replay")
        if res["decision"] != "ALLOW" or S.stats["scored"] % 5 == 0:
            await broadcast({"type": "txn", **res})
        if time.time() - last_stats > 0.5:
            last_stats = time.time()
            await broadcast(snapshot())
        await asyncio.sleep(1.0 / S.speed)
    await broadcast(snapshot())
    await broadcast({"type": "done"})


# ---- HTTP API -----------------------------------------------------------------
@app.get("/")
def index():
    return FileResponse(STATIC / "index.html")


@app.get("/api/health")
def health():
    return {"accelerator": engine.accel, "metrics": engine.metrics, "latency": engine.latency_stats(),
            "thresholds": {"step_up": engine.thr_step, "hold": engine.thr_hold},
            "model_sha256": engine.model_sha256[:16], "auth": bool(API_KEY)}


Account = Annotated[str, StringConstraints(min_length=1, max_length=32, pattern=r"^[A-Za-z0-9_.@-]+$")]


class CallIn(BaseModel):
    model_config = ConfigDict(extra="forbid")
    user: Account
    transcript: str = Field(min_length=1, max_length=5000)


class TxnIn(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    sender: Account
    receiver: Account
    amount: float = Field(gt=0, le=1e8)
    device: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_.:-]+$")

    @model_validator(mode="after")
    def distinct(self):
        if self.sender == self.receiver:
            raise ValueError("sender and receiver must differ")
        return self


class Control(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)
    speed: float | None = Field(default=None, ge=1, le=500)
    paused: bool | None = None


@app.post("/api/call", dependencies=[Depends(require_key)])
async def post_call(c: CallIn):
    r = engine.score_call(c.user, S.sim_ts, c.transcript)
    await broadcast({"type": "call", **r, "transcript": c.transcript, "manual": True})
    return r


@app.post("/api/txn", dependencies=[Depends(require_key)])
async def post_txn(t: TxnIn):
    S.sim_ts += 1
    d = {k: v for k, v in t.model_dump().items() if v is not None}
    d.update(ts=S.sim_ts, txn_id=f"LIVE-{uuid.uuid4().hex[:12]}")
    res = safe_score(engine.enrich(d))
    audit(res, "api")
    await broadcast({"type": "txn", **res, "manual": True})
    return res


@app.post("/api/control", dependencies=[Depends(require_key)])
def control(c: Control):
    if c.speed is not None:
        S.speed = c.speed
    if c.paused is not None:
        S.paused = c.paused
    return snapshot()


@app.websocket("/ws")
async def ws(sock: WebSocket):
    if len(clients) >= MAX_WS:
        await sock.close(code=1013)
        return
    await sock.accept()
    if API_KEY:  # key arrives as the first message, never in the URL (URLs end up in logs)
        try:
            msg = json.loads(await asyncio.wait_for(sock.receive_text(), timeout=5))
            ok = key_ok(msg.get("key")) if isinstance(msg, dict) else False
        except Exception:
            ok = False
        if not ok:
            await sock.close(code=1008)
            return
    clients.add(sock)
    await sock.send_json({"type": "hello", **health()})
    try:
        while True:
            await sock.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        clients.discard(sock)
