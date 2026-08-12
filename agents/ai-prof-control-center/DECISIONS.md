# Decisions

1. The live Control Center checkout is not an autonomous task target.
2. Self-maintenance uses `/home/agent/projects/ai-prof-control-center-maintenance` on local base branch `maintenance/base`.
3. Autonomous scope is intentionally limited to mobile-control surfaces, tests, documentation, reports, CI and staged systemd templates.
4. Core authority files remain outside autonomous scope so the agent cannot grant itself more power.
5. GitHub repository merge and Ubuntu live activation are separate gates.
6. Production deploy remains disabled by default; any future production runner must use a bounded, auditable, single-purpose owner approval.
7. Telegram remains a parallel control/notification channel; ChatGPT Gateway must not replace it.
8. AK BERMET finalization is retried only after platform repair and must reuse existing evidence instead of repeating passed gates.
