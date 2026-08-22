# RESORT OS — SOURCE POLICY

## Authoritative sources

For product intent and governance, use the canonical Resort OS Knowledge under the target repository `knowledge/` and the exact task contract from Resort OS HQ.

For implementation reality, trust only current code plus direct evidence from tests, build output, database/API/config inspection, runtime checks, or other reproducible proof.

`knowledge/04_CURRENT_STATE.md` is the only canonical owner of factual implementation reality. `knowledge/05_DECISIONS_AND_BACKLOG.md` owns decision/backlog history, not an independent Current State snapshot.

## Precedence

1. Explicit owner decision.
2. Canonical Product Bible / Domain Rules / Architecture / AI Admin for their respective responsibilities.
3. Verified implementation evidence.
4. Current State.
5. Decisions/backlog/history.
6. Recovery artifacts and historical reports.

If sources conflict, do not reconcile by guessing. Record the conflict and fail closed.

## Recovery evidence

`recovery-artifacts/**` is reference evidence only. It may inform design or recovery, but it must not be promoted to current implementation without integration plus tests/evidence.

## Prohibited source behavior

- Do not use another project as Resort OS truth.
- Do not infer production capability from filenames, mock UI, routes, screenshots, comments, or planned API names.
- Do not convert `UNKNOWN/VALIDATE` into an implementation decision without evidence or owner approval.
- Do not silently rewrite canonical business/product rules to fit code.
