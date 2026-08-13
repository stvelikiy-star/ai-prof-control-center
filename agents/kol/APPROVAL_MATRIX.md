# KÖL APPROVAL MATRIX

## Можно выполнять без отдельного подтверждения владельца

- read-only audit;
- анализ кода и документации;
- scoped source edits внутри разрешённых файлов;
- локальные тесты, TypeScript checks и production build;
- подготовка patch/diff/PR;
- диагностические проверки, не меняющие production, DB, secrets или бизнес-правила.

## Требуется отдельное подтверждение / operation gate

- persistent database migration или SQL mutation;
- production deploy;
- изменение DNS/hosting/production infrastructure;
- работа с secret values, credentials, tokens, service-role keys;
- изменение цен, комиссий, payment/refund правил;
- изменение ролей/прав доступа как бизнес-решения;
- массовое изменение или удаление реальных данных;
- включение alcohol module;
- применение исторического Stage 21;
- изменение courier model вне отдельного утверждённого этапа;
- подключение нового платного внешнего сервиса.

## Database gate

До persistent DB repair обязательно:
1. подтверждённый backup;
2. точный migration scope;
3. rollback;
4. positive/adversarial role matrix;
5. post-migration verification.

Если любой пункт не доказан — `BLOCKED`.
