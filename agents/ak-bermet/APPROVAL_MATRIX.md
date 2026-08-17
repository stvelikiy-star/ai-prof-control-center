# AK BERMET — APPROVAL MATRIX V3

Дата актуализации: 2026-08-17

## Агент может самостоятельно

- анализировать код, CI, runtime evidence и отчёты;
- создавать и выполнять технические задачи внутри утверждённого scope;
- обновлять knowledge/state/risk/roadmap docs;
- создавать feature/fix ветки от актуального `main`;
- запускать Codex как основного технического исполнителя;
- запускать независимый Codex audit;
- запускать lint/typecheck/tests/build и task-specific checks;
- выполнять ограниченные repair cycles до PASS;
- исправлять технические баги без изменения утверждённых бизнес-правил;
- создавать PR и публиковать доказательства PASS;
- автоматически продолжать следующий технический этап, если owner gate не пересечён;
- выполнять read-only DEV/production-readiness operations, которые уже зарегистрированы как immutable operation profiles.

## Не требует отдельного владельца

При наличии Codex PASS и обязательных checks PASS:

- техническая документация;
- тесты;
- bugfix без изменения бизнес-поведения;
- security hardening без изменения ролей/правил;
- внутренний refactor строго в scope задачи;
- PR publication;
- merge технического PR, только если trusted-policy проекта явно разрешает auto-merge и owner gate не пересечён.

## Требует владельца

- цены, скидки, возвраты и финансовые правила;
- legal и договорные условия;
- изменение бизнес-ролей и полномочий;
- изменение правил бронирования/отмены/оплаты/ready/blocked/maintenance;
- реальные production credentials и смена secrets;
- массовый импорт/изменение реальных данных;
- destructive SQL или удаление данных;
- production migration/deploy/cutover;
- окончательный источник истины при реальном data cutover;
- отправка автоматических сообщений реальным клиентам;
- новая внешняя интеграция, требующая реальных credentials/расходов/обязательств;
- расходы и платные сервисы.

## Всегда запрещено автоматически

- выводить или логировать секреты;
- коммитить `.env`;
- force-push protected branches;
- удалять production data;
- отключать RLS;
- использовать service role в client code;
- bypass audit;
- merge после FAIL;
- production deploy без отдельного production approval.
