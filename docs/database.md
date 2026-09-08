# Схема бази даних

База даних призначена для фінансового Telegram-бота. Вона зберігає користувачів, їхні категорії витрат і створені транзакції.

## Технічна реалізація

База даних розгорнута в Neon і використовує PostgreSQL. Рядок підключення зчитується зі змінної середовища `DATABASE_URL` у файлі `.env`.

Застосунок підключається до бази асинхронно за допомогою SQLAlchemy та драйвера `asyncpg`. Під час запуску бота виконується перевірка з’єднання запитом `SELECT 1`. Якщо таблиць ще немає, застосунок автоматично створює `users`, `categories` і `transactions`.

## Таблиця `users`

Зберігає користувачів, які взаємодіють із Telegram-ботом.

Основні поля:

- `id` — внутрішній унікальний ідентифікатор користувача.
- `telegram_id` — унікальний ідентифікатор користувача в Telegram.
- `username` — ім’я користувача в Telegram, якщо воно доступне.
- `created_at` — дата й час створення запису.

## Таблиця `categories`

Зберігає категорії витрат користувача, наприклад «кава», «продукти» або «транспорт».

Основні поля:

- `id` — внутрішній унікальний ідентифікатор категорії.
- `user_id` — посилання на користувача-власника категорії.
- `name` — назва категорії.
- `created_at` — дата й час створення категорії.

Один користувач не може мати дві категорії з однаковою назвою.

## Таблиця `transactions`

Зберігає окремі фінансові операції — витрати, додані через бота.

Основні поля:

- `id` — внутрішній унікальний ідентифікатор транзакції.
- `user_id` — посилання на користувача, який створив транзакцію.
- `category_id` — посилання на категорію витрат.
- `amount` — сума витрати.
- `description` — необов’язковий опис витрати.
- `created_at` — дата й час створення транзакції.

## Додавання витрати

Команда має формат:

```text
/expense <сума> <категорія>
```

Наприклад:

```text
/expense 120 кава
```

Бот перевіряє формат команди та додатне значення суми. Якщо користувача або його категорії ще немає, він створює їх. Потім бот додає запис до `transactions` і надсилає користувачу підтвердження про успішне збереження.

## Зв’язки між таблицями

- Один запис у `users` може мати багато записів у `categories`.
- Один запис у `users` може мати багато записів у `transactions`.
- Один запис у `categories` може мати багато записів у `transactions`.
- Кожна `transaction` належить одному користувачу та одній категорії цього користувача.

## Схема для dbdiagram.io

Скопіюйте код нижче та вставте його у [dbdiagram.io](https://dbdiagram.io/).

```dbml
Table users {
  id bigint [pk, increment]
  telegram_id bigint [not null, unique]
  username varchar(255)
  created_at timestamp [not null, default: `now()`]
}

Table categories {
  id bigint [pk, increment]
  user_id bigint [not null, ref: > users.id]
  name varchar(100) [not null]
  created_at timestamp [not null, default: `now()`]

  indexes {
    (user_id, name) [unique]
  }
}

Table transactions {
  id bigint [pk, increment]
  user_id bigint [not null, ref: > users.id]
  category_id bigint [not null, ref: > categories.id]
  amount decimal(12, 2) [not null]
  description varchar(255)
  created_at timestamp [not null, default: `now()`]
}
```
