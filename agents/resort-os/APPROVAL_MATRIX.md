# RESORT OS — APPROVAL MATRIX

## Normal bounded task authority

Allowed when inside exact approved scope and required checks:

- read permitted project files;
- edit permitted source/docs/tests/config files inside task scope;
- create bounded new files inside permitted scope;
- run approved local static checks/tests/builds;
- produce implementation evidence and independent audit evidence.

Normal tasks have no authority to commit, push, merge, deploy, access secrets, mutate production, or perform destructive database operations.

## Owner gate required

Explicit owner approval is required for:

- production deployment or production environment mutation;
- production database migrations/writes, destructive operations, restore/cutover;
- secrets/credentials provisioning or rotation;
- payment provider activation, financial settlement/refund behavior, or material financial rules;
- legal/compliance decisions;
- changing Product Vision or canonical protected Product/Domain/Architecture/AI documents;
- changing the human final-confirmation requirement for reservations;
- changing Partner/Agent approval/business policy;
- irreversible infrastructure actions;
- final production go-live approval.

## AI permission boundary

`AI_PERMISSION <= CURRENT_USER_PERMISSION` always. AI must use controlled tools/domain APIs and may not obtain unrestricted production DB access.

## Critical operations

Financial or critical actions require preview/confirmation or human approval according to the canonical AI Admin policy. Deterministic critical business logic must remain outside the LLM.
