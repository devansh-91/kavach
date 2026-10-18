"""In-transaction scoring engine: features -> model -> decision -> reasons -> network response.

On IBM z16/z17 (LinuxONE) Snap ML's import_model(tree_format="auto") compiles the boosted
trees to zDNN tensors and runs inference on the on-chip Integrated Accelerator for AI (Telum).
Elsewhere it falls back to compressed trees on CPU. Same code, same model file.
"""
import hashlib
import json
import os
import platform
import time
from collections import defaultdict, deque
from pathlib import Path

import numpy as np
import pandas as pd
from snapml import BoostingMachineClassifier

from .calls import train_call_model
from .features import FEATURES, LABELS, FeatureStore

ART = Path(__file__).resolve().parent.parent / "artifacts"
MODEL = ART / "kavach_gbm.pmml"
WATCH_SENDERS = 2        # held payments from >= 2 different senders within 24h -> mule watchlist
WATCH_WINDOW = 86400.0
WATCH_TTL = 3 * 86400.0  # watchlist entries expire unless an analyst confirms them
NEW_DEVICE_LIMIT = 10000  # first payment to a new payee from a new device above this needs re-verification
LOW_IS_RISKY = np.array([f in ("sender_age", "receiver_age", "recv_hist_in", "receiver_is_merchant",
                                "sender_mins_since_in") for f in FEATURES])
IS_AGE = np.array([f in ("sender_age", "receiver_age") for f in FEATURES])
YOUNG = np.log1p(90)  # "new account" only means < 90 days
SCAM_CALL = 0.8
I_CALL, I_NEW, I_DEV = (FEATURES.index(f) for f in ("scam_call_risk_2h", "new_payee", "new_device"))


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def read_csv(path):
    """Data files are plain CSV (no pickle), parsed with exact float round-trip."""
    return pd.read_csv(path, float_precision="round_trip", keep_default_na=False)


def accelerator_info(model):
    fmt = getattr(model.booster_, "optimized_tree_format_", "unknown")
    return {"arch": platform.machine(), "tree_format": fmt,
            "on_chip_ai": fmt == "zdnn_tensors",
            "engine": "IBM Z Integrated Accelerator for AI (zDNN)" if fmt == "zdnn_tensors" else "CPU compressed trees"}


class Engine:
    def __init__(self, tree_format=None, warmup=True):
        # auto = Telum AI accelerator (zDNN) when present, else CPU; override with KAVACH_TREE_FORMAT
        tree_format = tree_format or os.environ.get("KAVACH_TREE_FORMAT", "auto")
        meta = json.loads((ART / "meta.json").read_text())
        self.thr_step, self.thr_hold = meta["thr_step"], meta["thr_hold"]
        self.baseline = np.asarray(meta["baseline"], dtype=np.float32)
        self.metrics = meta["metrics"]
        # refuse to serve a model file that differs from the one that was trained and evaluated
        self.model_sha256 = sha256(MODEL)
        if self.model_sha256 != meta["model_sha256"]:
            raise RuntimeError(f"model integrity check failed for {MODEL}")
        self.model = BoostingMachineClassifier()
        self.model.import_model(str(MODEL), "pmml", tree_format=tree_format)
        self.accel = accelerator_info(self.model)
        self.call_model = train_call_model()  # deterministic, ~1 s; avoids loading pickled code
        self.fs = FeatureStore()
        self.holds_into = defaultdict(deque)   # receiver -> ts of held payments
        self.watchlist = {}                     # acct -> ts added
        self.latencies = deque(maxlen=5000)
        self.accounts = {}                      # acct -> (age_days, ts_seen, type)
        if warmup:
            self._warmup()

    def _warmup(self):
        tx = read_csv(ART / "history_txns.csv.gz").to_dict("records")
        calls = read_csv(ART / "history_calls.csv.gz")
        risk = self.call_model.predict_proba(calls.transcript)[:, 1]
        for c, r in zip(calls.to_dict("records"), risk):
            self.fs.observe_call(c["user"], c["ts"], float(r))
        for t in tx:
            self._remember(t)
            self.fs.observe(t)

    def _remember(self, t):
        self.accounts[t["sender"]] = (t["sender_age"], t["ts"], t["sender"][0])
        self.accounts[t["receiver"]] = (t["receiver_age"], t["ts"], t["receiver_type"])

    def enrich(self, t):
        """Fill account metadata for a manually submitted payment (a bank would read this from core banking)."""
        for side in ("sender", "receiver"):
            age, seen, typ = self.accounts.get(t[side], (0.5, t["ts"], t[side][:1]))
            t.setdefault(f"{side}_age", age + (t["ts"] - seen) / 86400)
            if side == "receiver":
                t.setdefault("receiver_type", typ)
        t.setdefault("device", f"D{t['sender']}_0")
        return t

    # ---- calls ------------------------------------------------------
    def score_call(self, user, ts, transcript):
        # too little speech to judge (silence, a hello): don't let the classifier's prior leak into payments
        risk = float(self.call_model.predict_proba([transcript])[0, 1]) if len(transcript.split()) >= 5 else 0.0
        self.fs.observe_call(user, ts, risk)
        return {"user": user, "ts": ts, "risk": risk, "scam": risk >= SCAM_CALL}

    # ---- transactions -----------------------------------------------
    def _reasons(self, x, p, k=3):
        """What-if attribution: reset each feature to a typical legit value and see how much risk drops."""
        X = np.repeat(x[None, :], len(FEATURES), axis=0)
        X[np.arange(len(FEATURES)), np.arange(len(FEATURES))] = self.baseline
        drop = p - self.model.predict_proba(X)[:, 1]
        # only report a feature when its value sits on the risky side of normal
        risky = np.where(LOW_IS_RISKY, x < self.baseline, x > self.baseline) & (~IS_AGE | (x < YOUNG))
        drop = np.where(risky, drop, 0.0)
        idx = np.argsort(-drop)[:k]
        return [{"feature": FEATURES[i], "label": LABELS[FEATURES[i]], "impact": round(float(drop[i]), 3)}
                for i in idx if drop[i] > 0.02]

    def score(self, t, explain=True):
        t0 = time.perf_counter()
        x = np.asarray(self.fs.features(t), dtype=np.float32)
        p = float(self.model.predict_proba(x[None, :])[0, 1])
        ms = (time.perf_counter() - t0) * 1000
        self.latencies.append(ms)

        s, r, ts = t["sender"], t["receiver"], t["ts"]
        decision = "HOLD" if p >= self.thr_hold else "STEP_UP" if p >= self.thr_step else "ALLOW"
        network = []
        # policy guardrail: a likely scam call + first payment to someone new always gets a cooling-off
        if decision == "ALLOW" and x[I_CALL] >= SCAM_CALL and x[I_NEW] and t["amount"] >= 1000:
            decision, p = "STEP_UP", max(p, self.thr_step)
            network.append("Scam call in last 2h + first payment to new payee")
        if decision == "ALLOW" and x[I_DEV] and x[I_NEW] and t["amount"] >= NEW_DEVICE_LIMIT:
            decision, p = "STEP_UP", max(p, self.thr_step)
            network.append("New device + first payment to new payee (device cooling-off)")
        # watchlist protects further victims (inflows held). The suspect's own outflows are only stepped
        # up, never frozen, so an attacker who pushes risky money into an innocent account can't lock them out.
        if self._watched(r, ts) and decision != "HOLD":
            decision, p = "HOLD", max(p, self.thr_hold)
            network.append("Receiver is on mule watchlist (pending analyst review)")
        if self._watched(s, ts) and decision == "ALLOW":
            decision, p = "STEP_UP", max(p, self.thr_step)
            network.append("Sender is a suspected mule - verify before outflow")

        newly_watched = None
        if decision == "HOLD":
            q = self.holds_into[r]
            q.append((ts, s))
            while q and q[0][0] < ts - WATCH_WINDOW:
                q.popleft()
            if (len({x_[1] for x_ in q}) >= WATCH_SENDERS and not self._watched(r, ts)
                    and t["receiver_type"] != "M"):
                self.watchlist[r] = ts
                newly_watched = r
        self._remember(t)
        self.fs.observe(t)

        reasons = self._reasons(x, p) if explain and decision != "ALLOW" else []
        reasons = [{"feature": "policy", "label": n, "impact": None} for n in network] + reasons
        return {"txn_id": t.get("txn_id"), "ts": ts, "sender": s, "receiver": r, "amount": t["amount"],
                "risk": round(p, 4), "decision": decision, "reasons": reasons,
                "latency_ms": round(ms, 3), "new_watchlist": newly_watched,
                "label": t.get("label"), "kind": t.get("kind")}

    def _watched(self, acct, ts):
        added = self.watchlist.get(acct)
        if added is None:
            return False
        if ts - added > WATCH_TTL:
            del self.watchlist[acct]
            return False
        return True

    def latency_stats(self):
        if not self.latencies:
            return {}
        a = np.asarray(self.latencies)
        return {"p50_ms": float(np.percentile(a, 50)), "p99_ms": float(np.percentile(a, 99)), "n": len(a)}
