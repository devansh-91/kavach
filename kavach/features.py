"""Streaming feature store.

`features(txn)` only reads state built from earlier events, then `observe(txn)` updates it,
so the same code path is used for offline training and live, in-transaction scoring.
"""
import math
from collections import defaultdict, deque

H, D = 3600.0, 86400.0

FEATURES = [
    "log_amount", "amt_vs_sender_avg", "amt_vs_sender_max", "sender_txn_1h", "sender_amt_1h",
    "sender_payees_1h", "new_payee", "new_device", "night", "hour", "sender_age", "receiver_age",
    "receiver_is_merchant", "recv_senders_24h", "recv_in_amt_24h", "recv_firsttime_payers_24h",
    "recv_hist_in", "sender_in_amt_1h", "sender_passthrough_1h", "sender_mins_since_in",
    "scam_call_risk_2h",
]

LABELS = {
    "log_amount": "Large amount", "amt_vs_sender_avg": "Amount far above sender's usual",
    "amt_vs_sender_max": "Bigger than any past payment", "sender_txn_1h": "Burst of payments in last hour",
    "sender_amt_1h": "High outflow in last hour", "sender_payees_1h": "Paying many people quickly",
    "new_payee": "First payment to this receiver", "new_device": "New / unseen device",
    "night": "Odd hour (12-5 AM)", "hour": "Unusual time of day", "sender_age": "Sender account age",
    "receiver_age": "Receiver account is very new", "receiver_is_merchant": "Receiver is not a merchant",
    "recv_senders_24h": "Receiver collecting from many senders", "recv_in_amt_24h": "Receiver got large inflows today",
    "recv_firsttime_payers_24h": "Many first-time payers to receiver", "recv_hist_in": "Receiver has little history",
    "sender_in_amt_1h": "Sender just received large money", "sender_passthrough_1h": "Money passed straight through (mule)",
    "sender_mins_since_in": "Forwarding minutes after receiving", "scam_call_risk_2h": "Scam call shortly before payment",
}


def _log(x):
    return math.log1p(max(x, 0.0))


class FeatureStore:
    def __init__(self):
        self.out_q = defaultdict(deque)   # acct -> (ts, amt, receiver)
        self.in_q = defaultdict(deque)    # acct -> (ts, amt, sender, first_time)
        self.n_out = defaultdict(int)
        self.sum_out = defaultdict(float)
        self.max_out = defaultdict(float)
        self.n_in = defaultdict(int)
        self.last_in = {}
        self.payees = defaultdict(set)
        self.devices = defaultdict(set)
        self.calls = defaultdict(deque)   # user -> (ts, risk)

    @staticmethod
    def _prune(q, now, window=D):
        while q and q[0][0] < now - window:
            q.popleft()

    def observe_call(self, user, ts, risk):
        self.calls[user].append((ts, risk))

    def features(self, t):
        s, r, ts, amt = t["sender"], t["receiver"], t["ts"], t["amount"]
        oq, iq_s, iq_r = self.out_q[s], self.in_q[s], self.in_q[r]
        for q in (oq, iq_s, iq_r):
            self._prune(q, ts)
        cq = self.calls[s]
        self._prune(cq, ts, 2 * H)

        out_1h = [x for x in oq if x[0] >= ts - H]
        in_1h = [x for x in iq_s if x[0] >= ts - H]
        n = self.n_out[s]
        avg = self.sum_out[s] / n if n else 500.0
        in_amt_1h = sum(x[1] for x in in_1h)
        out_amt_1h = sum(x[1] for x in out_1h) + amt
        hour = (ts % D) / H
        return [
            _log(amt),
            amt / (avg + 1),
            amt / (self.max_out[s] + 1) if n else 1.0,
            len(out_1h),
            _log(out_amt_1h),
            len({x[2] for x in out_1h} | {r}),
            float(r not in self.payees[s]),
            float(bool(self.devices[s]) and t["device"] not in self.devices[s]),
            float(hour < 5),
            hour,
            _log(t["sender_age"]),
            _log(t["receiver_age"]),
            float(t["receiver_type"] == "M"),
            len({x[2] for x in iq_r}),
            _log(sum(x[1] for x in iq_r)),
            sum(1 for x in iq_r if x[3]),
            _log(self.n_in[r]),
            _log(in_amt_1h),
            min(out_amt_1h / in_amt_1h, 5.0) if in_amt_1h > 0 else 0.0,
            _log((ts - self.last_in[s]) / 60) if s in self.last_in else 10.0,
            max((x[1] for x in cq), default=0.0),
        ]

    def observe(self, t):
        s, r, ts, amt = t["sender"], t["receiver"], t["ts"], t["amount"]
        self.out_q[s].append((ts, amt, r))
        self.in_q[r].append((ts, amt, s, r not in self.payees[s]))
        self.n_out[s] += 1
        self.sum_out[s] += amt
        self.max_out[s] = max(self.max_out[s], amt)
        self.n_in[r] += 1
        self.last_in[r] = ts
        self.payees[s].add(r)
        self.devices[s].add(t["device"])
