#!/usr/bin/env bash
# Keep an SSH tunnel to the Kavach server on a LinuxONE VM open, reconnecting whenever it drops.
#   scripts/tunnel.sh linux1@<vm-ip> [local-port]      (default local port 8001)
# The server stays bound to 127.0.0.1 on the VM, so nothing is exposed to the internet.
host=${1:?usage: tunnel.sh user@vm-ip [local-port]}; port=${2:-8001}
key=${KAVACH_SSH_KEY:-$HOME/.ssh/linuxone}
while true; do
  ssh -i "$key" -N -o ServerAliveInterval=15 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes \
      -L "$port:127.0.0.1:8000" "$host"
  echo "$(date +%T) tunnel dropped, reconnecting in 3 s" >&2
  sleep 3
done
