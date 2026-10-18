#!/usr/bin/env bash
# One-shot setup on an IBM LinuxONE Community Cloud VM (Ubuntu or RHEL, s390x).
# numpy/pandas/scikit-learn have no s390x wheels on PyPI, so we take them from the distro
# and pip-install only Snap ML (ships s390x wheels with zDNN support) + the web stack, pinned.
set -euo pipefail
cd "$(dirname "$0")"
if command -v apt-get >/dev/null; then
  sudo apt-get update -y
  # libzdnn0 = IBM Z Deep Neural Network library; lets Snap ML run trees on the Telum AI accelerator (NNPA)
  sudo apt-get install -y python3-venv python3-pip python3-numpy python3-pandas python3-sklearn \
       python3-scipy libzdnn0
else
  sudo dnf install -y python3 python3-pip python3-numpy python3-pandas python3-scikit-learn \
       python3-scipy || sudo dnf install -y python3 python3-pip gcc gcc-c++ python3-devel
  sudo dnf install -y zdnn || echo "zDNN not available: Snap ML will use CPU trees"
fi
python3 -m venv --system-site-packages .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q snapml==1.17.2 fastapi==0.143.3 pydantic==2.14.1 uvicorn==0.54.0
echo "== arch: $(uname -m)"; grep -m1 -i "machine" /proc/cpuinfo || true
grep -qw nnpa /proc/cpuinfo && echo "== NNPA (Telum on-chip AI accelerator) available" || echo "== no NNPA on this machine"
mkdir -p artifacts && chmod 700 artifacts
.venv/bin/python -m kavach.train | tee artifacts/train_log.txt
.venv/bin/python -m kavach.bench
cat <<'MSG'
== start the server bound to localhost only, with an API key:
   export KAVACH_API_KEY=$(openssl rand -hex 16); echo $KAVACH_API_KEY
   .venv/bin/uvicorn kavach.server:app --host 127.0.0.1 --port 8000
== view it from your laptop through an SSH tunnel (no port opened to the internet):
   ssh -i ~/.ssh/linuxone -L 8000:127.0.0.1:8000 linux1@<vm-ip>
   then open http://localhost:8000/#key=<the key>
MSG
