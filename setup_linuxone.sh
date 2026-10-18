#!/usr/bin/env bash
# One-shot setup on an IBM LinuxONE Community Cloud VM (Ubuntu or RHEL, s390x).
# numpy/pandas/scikit-learn have no s390x wheels on PyPI, so we take them from the distro
# and pip-install only Snap ML (ships s390x wheels with zDNN support) + the web stack.
set -euo pipefail
cd "$(dirname "$0")"
if command -v apt-get >/dev/null; then
  sudo apt-get update -y
  sudo apt-get install -y python3-venv python3-pip python3-numpy python3-pandas python3-sklearn \
       python3-scipy python3-joblib
else
  sudo dnf install -y python3 python3-pip python3-numpy python3-pandas python3-scikit-learn \
       python3-scipy python3-joblib || sudo dnf install -y python3 python3-pip gcc gcc-c++ python3-devel
fi
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q snapml fastapi uvicorn
echo "== arch: $(uname -m)"; grep -m1 -i "machine" /proc/cpuinfo || true
.venv/bin/python -m kavach.train | tee artifacts_train.log
.venv/bin/python -m kavach.bench
echo "== start: .venv/bin/uvicorn kavach.server:app --host 0.0.0.0 --port 8000"
