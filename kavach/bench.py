"""Latency / throughput benchmark. On LinuxONE it compares CPU trees vs the Telum on-chip
AI accelerator (zdnn_tensors) for the same model file.

    python -m kavach.bench
"""
import json
import platform
import time

import numpy as np
import pandas as pd

from .engine import ART, Engine

FORMATS = ["compress_trees"] + (["zdnn_tensors"] if platform.machine() == "s390x" else [])


def run(tree_format, txns, batch_sizes=(1, 64, 1024)):
    e = Engine(tree_format=tree_format)
    feats = []
    lat = []
    for t in txns:  # full in-transaction path: features + inference + decision, one at a time
        t0 = time.perf_counter()
        e.score(t, explain=False)
        lat.append((time.perf_counter() - t0) * 1e6)
    for t in txns:
        feats.append(e.fs.features(t))
    X = np.asarray(feats, dtype=np.float32)
    thr = {}
    for bs in batch_sizes:
        n = (len(X) // bs) * bs
        t0 = time.perf_counter()
        for i in range(0, n, bs):
            e.model.predict_proba(X[i:i + bs])
        thr[f"batch_{bs}_txn_per_s"] = round(n / (time.perf_counter() - t0))
    lat = np.asarray(lat)
    return {"tree_format": e.accel["tree_format"], "engine": e.accel["engine"],
            "e2e_p50_us": round(float(np.percentile(lat, 50)), 1),
            "e2e_p99_us": round(float(np.percentile(lat, 99)), 1), **thr}


def main():
    txns = pd.read_pickle(ART / "replay_txns.pkl").head(20000).to_dict("records")
    out = {"arch": platform.machine(), "cpu": platform.processor() or platform.platform(),
           "n_txns": len(txns), "results": []}
    for fmt in FORMATS:
        try:
            out["results"].append(run(fmt, [dict(t) for t in txns]))
        except Exception as ex:  # accelerator not exposed to this VM
            out["results"].append({"tree_format": fmt, "error": str(ex)})
    print(json.dumps(out, indent=2))
    (ART / f"bench_{out['arch']}.json").write_text(json.dumps(out, indent=2))


if __name__ == "__main__":
    main()
