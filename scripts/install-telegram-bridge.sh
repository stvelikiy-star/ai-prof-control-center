#!/usr/bin/env bash
set -euo pipefail

source_root=/home/agent/projects/ai-prof-control-center
unit_source="${source_root}/systemd/ai-prof-telegram-bridge.service"
unit_directory="${XDG_CONFIG_HOME:-${HOME}/.config}/systemd/user"
unit_target="${unit_directory}/ai-prof-telegram-bridge.service"
config_directory="${XDG_CONFIG_HOME:-${HOME}/.config}/ai-prof-control-center"
config_target="${config_directory}/telegram.env"
example_source="${source_root}/config/telegram.env.example"
state_directory="${XDG_STATE_HOME:-${HOME}/.local/state}/ai-prof-control-center"

install -d -m 0700 "${unit_directory}" "${config_directory}" "${state_directory}"
install -m 0600 "${unit_source}" "${unit_target}"
if [[ ! -e "${config_target}" ]]; then
    install -m 0600 "${example_source}" "${config_target}.example"
fi
systemctl --user daemon-reload
systemctl --user enable ai-prof-telegram-bridge.service

if [[ -f "${config_target}" ]] \
    && grep -Eq '^AI_PROF_TELEGRAM_BOT_TOKEN=[0-9]{6,12}:[A-Za-z0-9_-]{20,}$' "${config_target}" \
    && grep -Eq '^AI_PROF_TELEGRAM_REPORT_CHAT_ID=-?[0-9]+$' "${config_target}" \
    && grep -Eq '^AI_PROF_TELEGRAM_OWNER_USER_ID=[0-9]+$' "${config_target}"; then
    systemctl --user restart ai-prof-telegram-bridge.service
    printf '%s\n' "Installed, enabled, and started ai-prof-telegram-bridge.service."
else
    printf '%s\n' "Installed and enabled ai-prof-telegram-bridge.service; not started because real credentials are absent."
fi
