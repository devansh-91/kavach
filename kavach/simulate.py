"""Synthetic UPI world: normal users, merchants, kirana shops, and scam campaigns
(digital-arrest / KYC impersonation, account takeover) cashed out through mule chains.

Every event is ordered by time so features can be computed strictly from the past.
"""
import random
import numpy as np
import pandas as pd

from .calls import scam_transcript, legit_transcript

DAY = 86400
HOUR = 3600
MIN = 60


class World:
    def __init__(self, seed=7, n_users=5000, n_merchants=300, n_kirana=60,
                 n_mules=90, n_cashout=25, days=7, n_campaigns=320):
        self.rng = random.Random(seed)
        self.np = np.random.default_rng(seed)
        self.days = days
        self.n_campaigns = n_campaigns
        self.txns, self.calls = [], []
        self.users = [f"U{i:05d}" for i in range(n_users)]
        self.merchants = [f"M{i:04d}" for i in range(n_merchants)]
        self.kirana = [f"K{i:03d}" for i in range(n_kirana)]
        self.mules = [f"X{i:03d}" for i in range(n_mules)]
        self.cashout = [f"C{i:03d}" for i in range(n_cashout)]
        r = self.rng
        self.age = {}
        for u in self.users:
            self.age[u] = r.randint(1, 40) if r.random() < 0.10 else r.randint(60, 3000)
        for m in self.merchants:
            self.age[m] = r.randint(200, 4000)
        for k in self.kirana:
            self.age[k] = r.randint(20, 2500)
        for x in self.mules + self.cashout:
            self.age[x] = r.randint(2, 45) if r.random() < 0.5 else r.randint(60, 1500)
        self.contacts = {u: r.sample(self.users, r.randint(4, 10)) for u in self.users}
        self.fav_merchants = {u: r.sample(self.merchants, 8) + r.sample(self.kirana, 2) for u in self.users}
        self.spend = {u: float(np.exp(self.np.normal(6.0, 0.7))) for u in self.users}  # typical ticket ~ Rs 400
        self.rate = {u: r.uniform(1.0, 5.0) for u in self.users}
        self.dev_hist = {u: [(0, f"D{u}_0")] for u in self.users + self.mules + self.cashout + self.kirana}
        self.renter = {u: r.choice(self.users) for u in self.users if r.random() < 0.25}

    # ---- helpers -------------------------------------------------------
    def _txn(self, ts, s, d, amt, label=0, kind="normal", device=None, campaign=-1):
        self.txns.append(dict(ts=float(ts), sender=s, receiver=d, amount=round(float(amt), 2),
                              device=device or self.device_at(s, ts), label=label, kind=kind,
                              sender_age=self.age[s] + ts / DAY, receiver_age=self.age[d] + ts / DAY,
                              receiver_type=d[0], campaign=campaign))

    def device_at(self, u, ts):
        dev = self.dev_hist[u][0][1]
        for start, d in self.dev_hist[u]:
            if ts >= start:
                dev = d
        return dev

    def _active_ts(self, day):
        r = self.rng
        h = r.choices(range(24), weights=[1, 1, 1, 1, 1, 2, 4, 6, 8, 9, 9, 9, 10, 9, 8, 8, 9, 10, 11, 11, 10, 8, 5, 2])[0]
        return day * DAY + h * HOUR + r.random() * HOUR

    # ---- normal behaviour ---------------------------------------------
    def normal(self):
        r = self.rng
        for u in self.users:
            for day in range(self.days):
                if r.random() < 0.004:  # legit phone change, device persists afterwards
                    self.dev_hist[u].append((day * DAY, f"D{u}_{day + 1}"))
                for _ in range(self.np.poisson(self.rate[u])):
                    ts = self._active_ts(day)
                    x = r.random()
                    if x < 0.68:
                        d = r.choice(self.fav_merchants[u]) if r.random() < 0.85 else r.choice(self.merchants + self.kirana)
                        amt = self.spend[u] * float(np.exp(self.np.normal(0, 0.6)))
                    elif x < 0.95:
                        d = r.choice(self.contacts[u])
                        amt = self.spend[u] * float(np.exp(self.np.normal(0.4, 0.8)))
                    else:  # legit new payee, sometimes big (used bike, hospital, contractor)
                        d = r.choice(self.users)
                        amt = self.spend[u] * float(np.exp(self.np.normal(2.0, 1.5)))
                        if r.random() < 0.15:
                            amt = r.choice([20000, 45000, 80000, 150000]) * r.uniform(0.7, 1.1)
                    if d != u:
                        self._txn(ts, u, d, max(amt, 1))
            if u in self.renter and r.random() < self.days / 30 * 1.5:  # rent day falls in window
                self._txn(self._active_ts(r.randrange(self.days)), u, self.renter[u],
                          r.choice([8000, 12000, 15000, 22000, 35000]))
        # friends pool money for a trip, organiser pays a travel agent minutes later (mule-like, legit)
        for _ in range(int(len(self.users) * 0.03 * self.days)):
            org = r.choice(self.users)
            ts = self._active_ts(r.randrange(self.days))
            total = 0
            for f in r.sample(self.users, r.randint(3, 9)):
                if f != org:
                    a = r.choice([1500, 2000, 3000, 5000, 8000])
                    self._txn(ts, f, org, a)
                    total += a
                    ts += r.uniform(1, 20) * MIN
            self._txn(ts + r.uniform(3, 60) * MIN, org, r.choice(self.users + self.merchants), total * r.uniform(0.9, 1.1))
        # night owls / phone changes at night (legit)
        for _ in range(int(len(self.users) * 0.02 * self.days)):
            u = r.choice(self.users)
            ts = r.randrange(self.days) * DAY + r.uniform(0, 5) * HOUR
            self._txn(ts, u, r.choice(self.users + self.merchants), self.spend[u] * r.uniform(1, 30),
                      device=f"Dnight{u}{int(ts)}" if r.random() < 0.3 else None)
        # kirana owners settle to wholesalers (cash-out-like but legit)
        for k in self.kirana:
            for day in range(self.days):
                self._txn(self._active_ts(day), k, r.choice(self.merchants), r.uniform(3000, 40000))
        # mules look boring most of the time
        for x in self.mules + self.cashout:
            for day in range(self.days):
                for _ in range(self.np.poisson(1.2)):
                    self._txn(self._active_ts(day), x, r.choice(self.merchants), r.uniform(50, 900))
        # legit calls (bank, delivery, family) - some mention bank/OTP words
        for _ in range(int(len(self.users) * 0.4 * self.days)):
            u = r.choice(self.users)
            self.calls.append(dict(ts=self._active_ts(r.randrange(self.days)), user=u,
                                   transcript=legit_transcript(r), label=0))

    # ---- fraud campaigns ----------------------------------------------
    def _layer(self, mule, ts, amt, campaign, depth=0):
        """Mule forwards most of the inflow within minutes, sometimes via another mule."""
        r = self.rng
        out_ts = ts + r.uniform(2, 35) * MIN
        out_amt = amt * r.uniform(0.85, 0.98)
        if depth == 0 and r.random() < 0.35:
            nxt = r.choice([m for m in self.mules if m != mule])
            self._txn(out_ts, mule, nxt, out_amt, 1, "mule_layering", campaign=campaign)
            self._layer(nxt, out_ts, out_amt, campaign, depth + 1)
        else:
            parts = r.choice([1, 1, 2, 3])
            for p in range(parts):
                self._txn(out_ts + p * r.uniform(1, 6) * MIN, mule, r.choice(self.cashout),
                          out_amt / parts, 1, "mule_cashout", campaign=campaign)

    def frauds(self):
        r = self.rng
        for c in range(self.n_campaigns):
            victim = r.choice(self.users)
            mule = r.choice(self.mules)
            day = r.randrange(self.days)
            kind = r.choices(["digital_arrest", "kyc_scam", "ato"], weights=[0.45, 0.3, 0.25])[0]
            if kind == "ato":
                ts = day * DAY + r.uniform(0.5, 5) * HOUR  # night
                dev = f"DATO{c}"
                for i in range(r.randint(1, 4)):
                    amt = r.uniform(5000, 60000)
                    self._txn(ts, victim, mule, amt, 1, "ato", device=dev, campaign=c)
                    self._layer(mule, ts, amt, c)
                    ts += r.uniform(1, 10) * MIN
                continue
            ts = self._active_ts(day)
            if r.random() < 0.55:  # call only captured when the user has opted in to call protection
                self.calls.append(dict(ts=ts, user=victim, transcript=scam_transcript(r, kind), label=1))
            ts += r.uniform(4, 40) * MIN
            if r.random() < 0.3:  # small "verification" transfer first
                self._txn(ts, victim, mule, r.choice([1, 2, 5, 10]), 1, kind + "_test", campaign=c)
                ts += r.uniform(2, 10) * MIN
            base = r.choice([2000, 5000, 9999, 15000, 25000, 49999, 75000, 99999, 150000])
            if r.random() < 0.25:  # repeat victim: mule is already a known payee
                self._txn(ts - r.uniform(1, 3) * DAY, victim, mule, r.uniform(200, 2000), campaign=c)
            for i in range(r.randint(1, 3)):
                amt = base * r.uniform(0.6, 1.2)
                self._txn(ts, victim, mule, amt, 1, kind, campaign=c)
                self._layer(mule, ts, amt, c)
                ts += r.uniform(5, 45) * MIN

    def build(self):
        self.normal()
        self.frauds()
        tx = pd.DataFrame(self.txns).sort_values("ts").reset_index(drop=True)
        tx.insert(0, "txn_id", [f"T{i:07d}" for i in range(len(tx))])
        calls = pd.DataFrame(self.calls).sort_values("ts").reset_index(drop=True)
        return tx, calls


if __name__ == "__main__":
    tx, calls = World().build()
    print(tx.shape, tx.label.mean(), tx.kind.value_counts().to_dict())
    print(calls.shape, calls.label.mean())
