#!/usr/bin/env bash
set -euo pipefail

unit_source=/home/agent/projects/ai-prof-control-center/systemd/ai-prof-control-center.service
unit_directory="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
unit_target="${unit_directory}/ai-prof-control-center.service"
state_directory=/home/agent/.local/state/ai-prof-control-center

install -d -m 0700 "${unit_directory}"
install -d -m 0700 "${state_directory}" "${state_directory}/queue" "${state_directory}/logs/orchestrator" "${state_directory}/run"
for queue_name in pending active review pending_codex approved blocked failed cancelled completed; do
    install -d -m 0700 "${state_directory}/queue/${queue_name}"
done
install -m 0600 "${unit_source}" "${unit_target}"
systemctl --user daemon-reload
systemctl --user enable ai-prof-control-center.service
printf '%s\n' "Installed and enabled ai-prof-control-center.service; it was not started."
