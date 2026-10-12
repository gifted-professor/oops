#!/bin/sh
# Install or update the Oops Mac helper (runs from ~/.oops-agent: launchd cannot rely on the project volume).
set -eu
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/../.." && pwd)
mkdir -p "$HOME/.oops-agent" && chmod 700 "$HOME/.oops-agent"
[ -x "$HOME/.oops-agent/venv/bin/python" ] || /opt/homebrew/opt/python@3.14/bin/python3.14 -m venv "$HOME/.oops-agent/venv"
"$HOME/.oops-agent/venv/bin/python" -c 'import pptx' 2>/dev/null || "$HOME/.oops-agent/venv/bin/pip" install -q python-pptx
cp "$ROOT/scripts/oops_mac_agent.py" "$ROOT/scripts/oops_slides.py" "$HOME/.oops-agent/"
[ -f "$HOME/.oops-agent/config.json" ] || { echo "缺少 ~/.oops-agent/config.json（server、token）"; exit 1; }
chmod 600 "$HOME/.oops-agent/config.json"
cp "$ROOT/deploy/mac/local.oops.mac-agent.plist" "$HOME/Library/LaunchAgents/"
launchctl bootout "gui/$(id -u)/local.oops.mac-agent" 2>/dev/null || true
launchctl bootstrap "gui/$(id -u)" "$HOME/Library/LaunchAgents/local.oops.mac-agent.plist"
echo "已启动；日志 ~/Library/Logs/oops-mac-agent.log；暂停：touch ~/.oops-agent/paused"
