"""Generate the synthetic UPI world, build streaming features, train and evaluate.

    python -m kavach.train
"""
import json
import time
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import average_precision_score, roc_auc_score
from snapml import BoostingMachineClassifier

from .calls import train_call_model
from .engine import MODEL, sha256
from .features import FEATURES, FeatureStore
from .simulate import DAY, World

ART = Path(__file__).resolve().parent.parent / "artifacts"


def stream_features(tx, calls, call_model):
    """Replay calls + transactions in time order, exactly as the live engine sees them."""
    calls = calls.copy()
    calls["risk"] = call_model.predict_proba(calls.transcript)[:, 1]
    fs, rows = FeatureStore(), []
    ci, crow = 0, calls.to_dict("records")
    for t in tx.to_dict("records"):
        while ci < len(crow) and crow[ci]["ts"] <= t["ts"]:
            fs.observe_call(crow[ci]["user"], crow[ci]["ts"], crow[ci]["risk"])
            ci += 1
        rows.append(fs.features(t))
        fs.observe(t)
    return np.asarray(rows, dtype=np.float32), calls


def report(y, p, kinds, thr_step, thr_hold):
    out = {"pr_auc": float(average_precision_score(y, p)), "roc_auc": float(roc_auc_score(y, p))}
    for name, thr in (("step_up", thr_step), ("hold", thr_hold)):
        flag = p >= thr
        out[name] = {"threshold": thr, "recall": float(flag[y == 1].mean()),
                     "precision": float(y[flag].mean()) if flag.any() else 0.0,
                     "false_positive_rate": float(flag[y == 0].mean())}
    caught = p >= thr_step
    out["recall_by_kind"] = {k: float(caught[(kinds == k)].mean()) for k in sorted(set(kinds[y == 1]))}
    return out


def recall_at_fpr(y, p, fpr):
    thr = np.quantile(p[y == 0], 1 - fpr)
    return {"recall": float((p[y == 1] > thr).mean()), "fpr": float((p[y == 0] > thr).mean())}


def main():
    ART.mkdir(exist_ok=True)
    t0 = time.time()
    tx, calls = World().build()
    print(f"world: {len(tx):,} txns ({tx.label.mean():.2%} fraud), {len(calls):,} calls  [{time.time()-t0:.1f}s]")

    call_model = train_call_model()
    X, calls = stream_features(tx, calls, call_model)
    y = tx.label.values.astype(np.float32)
    print(f"features: {X.shape}  [{time.time()-t0:.1f}s]")

    split = tx.ts.values < 5 * DAY  # train on days 0-4, test on unseen days 5-6
    Xtr, ytr, Xte, yte = X[split], y[split], X[~split], y[~split]
    model = BoostingMachineClassifier(num_round=300, max_depth=6, learning_rate=0.1,
                                      colsample_bytree=0.8, subsample=0.8, n_jobs=4, random_state=0)
    model.fit(Xtr, ytr)
    model.export_model(str(MODEL), output_type="pmml")
    p = model.predict_proba(Xte)[:, 1]

    thr_step, thr_hold = 0.30, 0.80
    metrics = report(yte, p, tx.kind.values[~split], thr_step, thr_hold)
    call_p = call_model.predict_proba(calls.transcript)[:, 1]
    metrics["call_model_auc"] = float(roc_auc_score(calls.label, call_p))
    metrics["n_test_txns"] = int((~split).sum())
    metrics["n_test_fraud"] = int(yte.sum())
    print(json.dumps(metrics, indent=2))

    # ablation: what do the network (mule) + scam-call signals add over a classic per-txn model?
    fpr = 0.001
    graph = [FEATURES.index(f) for f in FEATURES if f.startswith(("recv_", "sender_in", "sender_pass", "sender_mins", "scam_call"))]
    keep = [i for i in range(len(FEATURES)) if i not in graph]
    base = BoostingMachineClassifier(num_round=300, max_depth=6, learning_rate=0.1, colsample_bytree=0.8,
                                     subsample=0.8, n_jobs=4, random_state=0).fit(Xtr[:, keep], ytr)
    pb = base.predict_proba(Xte[:, keep])[:, 1]
    rule = ((tx.amount.values[~split] > 20000) & (Xte[:, FEATURES.index("new_payee")] > 0)).astype(float)
    metrics["ablation_recall_at_0.1pct_fpr"] = {
        "rule_amount>20k_and_new_payee": {"recall": float(rule[yte == 1].mean()), "fpr": float(rule[yte == 0].mean())},
        "per_txn_model_only": recall_at_fpr(yte, pb, fpr),
        "kavach_full": recall_at_fpr(yte, p, fpr),
    }
    print(json.dumps(metrics["ablation_recall_at_0.1pct_fpr"], indent=2))

    meta = {"features": FEATURES, "baseline": np.median(Xtr[ytr == 0], axis=0).tolist(),
            "model_sha256": sha256(MODEL),
            "thr_step": thr_step, "thr_hold": thr_hold, "metrics": metrics}
    (ART / "meta.json").write_text(json.dumps(meta, indent=2))
    tx[~split].to_csv(ART / "replay_txns.csv.gz", index=False)
    calls[calls.ts >= 5 * DAY].drop(columns="risk").to_csv(ART / "replay_calls.csv.gz", index=False)
    # warm-up history (days 0-4) so the live feature store starts with realistic state
    tx[split].to_csv(ART / "history_txns.csv.gz", index=False)
    calls[calls.ts < 5 * DAY].drop(columns="risk").to_csv(ART / "history_calls.csv.gz", index=False)
    print(f"done [{time.time()-t0:.1f}s] -> {ART}")


if __name__ == "__main__":
    main()
