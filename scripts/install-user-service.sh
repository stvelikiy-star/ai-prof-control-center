#!/usr/bin/env bash
set -euo pipefail

unit_source=/home/agent/projects/ai-prof-control-center/systemd/ai-prof-control-center.service
unit_directory="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
unit_target="${unit_directory}/ai-prof-control-center.service"

install -d -m 0700 "${unit_directory}"
install -m 0600 "${unit_source}" "${unit_target}"
systemctl --user daemon-reload
systemctl --user enable ai-prof-control-center.service
printf '%s\n' "Installed and enabled ai-prof-control-center.service; it was not started."
