# AI Controlled Actions

## Мета

AI-чат може підготувати дію, але не змінює фінансові дані самостійно. У першій версії
доступна тільки дія `create_transaction`.

```text
AI Chat → pending action → картка React → confirm/cancel → backend validation → Neon → audit log
```

## Що може робити AI

Tool `propose_create_transaction` створює лише чернетку. Він потребує всіх полів:

- `type`: `income` або `expense`;
- додатну суму в гривнях;
- курс USD;
- головну категорію тільки `Роботи` або `Матеріали`;
- підкатегорію, опис і дату `YYYY-MM-DD`.

Якщо хоча б одного поля бракує або категорія інша, AI ставить уточнювальне запитання.
Він не створює операцію і не вигадує дані.

## Pending action

Таблиця `pending_ai_actions` зберігає `id`, користувача, проєкт, `thread_id`, тип,
валідаційний payload, хеш payload, статус і часові мітки. Чернетка дійсна 24 години.

Стани:

| Статус | Значення |
| --- | --- |
| `pending` | Чекає на дію користувача; не змінила ledger. |
| `processing` | Atomically захоплена confirm-запитом. |
| `confirmed` | Операцію успішно записано в `transactions`. |
| `cancelled` | Користувач відмовився; ledger не змінено. |
| `failed` | Backend-валідація або бізнес-операція не пройшла. |

Перехід із `pending` атомарний. Отже, другий клік confirm або cancel не може виконати
операцію двічі. Однаковий активний payload у межах thread не створює додаткову чернетку.

## Атомарний confirm

Під час confirm backend блокує pending action, повторно валідовує payload, створює транзакцію,
змінює status action на `confirmed` і додає audit-записи в одній транзакції БД.

`transactions.source_action_id` зберігає `action_id` і є унікальним для непорожніх значень.
Повторний confirm не створює дубль, а повертає вже створену транзакцію.

## API

- `POST /api/ai/chat` — у SSE може надійти `pending_action` з даними картки.
- `GET /api/ai/chat/threads/{thread_id}` — повертає незавершені дії поточного діалогу.
- `POST /api/ai/actions/{action_id}/confirm?project_id=<id>` — перевіряє право власності,
  строк дії, `pending`-стан, тип дії та строгий payload; лише тоді викликає звичайну
  бізнес-логіку `save_transaction`.
- `POST /api/ai/actions/{action_id}/cancel?project_id=<id>` — скасовує тільки власну
  активну чернетку.

LLM не викликає confirm/cancel endpoint-и, не генерує SQL і не отримує ідентифікатори
користувача або проєкту як аргументи tool.

## Backend validation

Payload повторно перевіряється `TransactionActionPayload` із `extra="forbid"` під час
створення чернетки та ще раз перед виконанням. Це перевіряє тип, суму, курс, дату,
дозволену категорію, текстові поля та відсутність зайвих ключів. Навіть коректний JSON
від моделі не обходить цю перевірку.

## Audit log

`ai_action_audit_log` містить `user_id`, `project_id`, `thread_id`, `action_id`, тип,
подію, безпечний результат і час. Події: `pending_created`, `confirm_requested`,
`confirmed`, `cancelled`, `failed`.

У log не записуються `GEMINI_API_KEY`, `DATABASE_URL`, `BOT_TOKEN`, паролі, токени чи
інші секрети.

## React

Картка відображає тип операції, суму, категорію/підкатегорію, дату, курс USD та опис.
Під час confirm/cancel обидві кнопки блокуються. Після успішного confirm React оновлює
баланс, операції та діаграми; після cancel картка зникає без зміни ledger.

## Розширення

Наступні action tools додаються лише за тим самим контрактом: окремий строгий payload,
pending action, повторна backend-валідація, атомарний confirm і audit log. Видалення,
масові зміни, платежі й довільний SQL не входять до поточного scope.
