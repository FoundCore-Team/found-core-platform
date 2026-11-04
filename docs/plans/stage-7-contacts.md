# Этап 7: contacts-notifications. Документ для исполнителя

Ветка: `feat/contacts`. Перед началом прочитать `CLAUDE.md` и `docs/rules.md`: они обязательны. Этот документ ничего не отменяет, он уточняет этап 7.

## 0. Как работать с этим документом

1. **Любое ключевое решение можно пересмотреть, но только осознанно.** Если выбираешь другой вариант или нашёл проблему, которой здесь нет, допиши в раздел 9 «Отклонения»: что изменил, почему, что отброшено.
2. Этап делается **тремя частями** (7a, 7b, 7c, раздел 6) без PR между ними: техлид коммитит сам. Каждая часть заканчивается зелёными тестами и `alembic check`, `main` между частями не ломается. PR — в конце.
3. Если пункт плана оказался неверным, исправь план в том же коммите, а не только код.
4. Тесты и `alembic check` запускает человек и прикладывает результат. «Работает» без прогона не считается.

## 1. Контекст и цель

Лента (этап 6) показывает анкеты без `username`: решение 9 этапа 6 оставило контакт этому этапу. Механика из `CLAUDE.md`: контакт открывается сразу по нажатию кнопки, без взаимности; владелец анкеты получает от бота уведомление, кто открыл его контакт.

Что нужно:
- `POST /profiles/{id}/contact` отдаёт `username` владельца;
- дневной лимит открытий (источник — таблица `contact_opens`, как и задумано в `CLAUDE.md`, «Data model»);
- уведомление владельцу через Telegram Bot API, которое не блокирует запрос и не теряется молча.

Сейчас в коде нет ни одного исходящего вызова Telegram: `BotConfig.token` используется только для проверки `initData`.

## 2. Ключевые решения и почему

Все решения 1–20 утверждены пользователем.

### Открытие контакта

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 1 | **«Открыт» — пара профилей** (мой активный → чужой). Первое открытие пары: запись, уведомление, −1 из лимита. Повтор снова отдаёт `username`, без записи, без уведомления, без лимита. | Мультипрофиль: каждая анкета — отдельное предложение, владельцу важно, на какую откликнулись. Бесплатный повтор не даёт превратить бота в спам-канал и позволяет фронтенду спокойно повторить запрос. | Каждое нажатие — новое открытие. Пара «профиль → человек» или «человек → человек». |
| 2 | **Открыть можно любой видимый профиль любой категории** — то же правило видимости, что у `GET /feed/cards/{id}`. Свой, несуществующий, невидимый → `404`. Уже открытый, но ставший невидимым (пауза, бан, нет username) → тоже `404`. | Tier 3 и диплинки показывают другие категории, их тоже должно быть можно открыть. Одно правило видимости для всего: пауза означает «не хочу новых контактов». | Только своя категория. Отдавать username при повторе, даже если анкета стала невидимой. |
| 3 | **Порядок проверок:** `409 username_required` → `409 no_active_profile` → `404` → повтор (`200`, лимит не проверяется) → `429` → `201`. | Как в ленте. Повтор работает и при исчерпанном лимите, ставший невидимым — `404` даже при повторе. | Лимит раньше `404`. |
| 4 | **Ответ:** `{username, is_new_open, remaining_today}`. **`201`** для нового открытия, **`200`** для повтора. `last_name` владельца не отдаётся. | Открытие создаёт запись. `remaining_today` нужен фронтенду для счётчика. Асимметрия с уведомлением (там `last_name` открывшего есть) осознанная: инициатор раскрывается полнее, полное имя владельца он увидит в Telegram. | Всегда `200`. `last_name` в ответе. |
| 5 | **Открытие контакта не пишет `profile_views`.** | Просмотр пишет только `POST /feed/views`, это задача фронтенда (`CLAUDE.md`, «Feed algorithm»). | Писать просмотр в той же транзакции. |
| 6 | **Флаг `is_contact_opened`** в карточках ленты и в `GET /feed/cards/{id}` для пары «активный профиль → анкета». Считается на каждый запрос, как `is_viewed`, в сессии не хранится. | Фронтенд показывает «Написать» вместо «Открыть контакт». Сам `username` в карточку не кладём: его даёт повторный бесплатный `POST`. | Без флага. |
| 7 | **Истории** («кого я открыл», «кто открыл меня») **не делаем** в этапе 7. | Данные уже есть в `contact_opens`, списки — отдельный PR, когда появится фронтенд. | Один или оба списка сейчас. |

### Лимит

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 8 | **Дневной лимит делается в этапе 7:** скользящие 24 часа на **пользователя** (по всем его профилям), **20** по умолчанию, в `ContactConfig`. Повторы не считаются. | Это продуктовое правило по `contact_opens`, а не rate limiting этапа 8 (тот — счётчики в Redis в минуту против злоупотреблений). Без лимита открытие контакта — бесплатный сбор username всех анкет. Лимит на пользователя: мультипрофиль его не обходит. Сброс не зависит от часового пояса. | Перенести в rate limiting. Календарный день (UTC или по таймзоне профиля). Лимит на профиль. 10 или 50. |
| 9 | **Лимит исчерпан:** `429 {"detail": {"code": "contact_limit_reached", "message", "limit", "resets_at"}}`, где `resets_at` = самое старое открытие в окне + 24 ч. | Фронтенд пишет «следующий контакт в 14:32». Тот же формат `detail`, что в ленте. | `429` без времени. |
| 10 | **Гонка на границе лимита.** Два параллельных открытия разных анкет на 19-м контакте оба видят «19 < 20». Защита: **`SELECT ... FOR UPDATE` на строку `users` открывшего** через новый узкий метод `IUserRepository.lock_for_update(user_id)`. Открытия одного человека сериализуются до коммита. | Лимит точный, это проверяется тестом на `asyncio.gather`. Тот же приём, что `FOR UPDATE` в `PUT /profiles`. | `pg_advisory_xact_lock`. Не защищаться. |
| 11 | **`contact_opens` удаляется каскадом по обоим профилям.** | Проще: два FK на `profiles`. Обход лимита через удаление своей анкеты — **принятый риск**, записать в «Risks» в `CLAUDE.md`. | `opener_user_id` и `SET NULL` по профилю ради лимита. |

### Уведомление

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 12 | **Текст:** только русский, plain text (без `parse_mode`). Полное имя открывшего (`first_name` и `last_name`, если он есть), «категория · роль» обеих анкет, `@username` открывшего. Пол неизвестен, поэтому «открыл(а)». Текст ниже. | Владелец может написать первым и не тратить свой лимит на обратное открытие. Plain text: имена не нужно экранировать, `@username` Telegram сделает ссылкой сам. | Без username. ru/en по `language_code`. Только `first_name`. Нейтральная формулировка без глагола. |
| 13 | **Кнопка** — inline `web_app` на `{mini_app_url}?profile_id={opener_profile_id}`. | Нужен только URL фронтенда, настраивать имя Mini App в BotFather не нужно. Работает в личном чате с ботом. | Ссылка `t.me/<bot>/<app>?startapp=`. Без ссылки. |
| 14 | **Каждое открытие — отдельное сообщение**, без дайджеста. | У каждого сообщения свой `@username` и своя кнопка. Спам ограничен лимитом открывающих и правилом «один раз на пару». Дайджест — в риски. | Дайджест раз в N минут. |
| 15 | **Надёжность:** `notification_status` в `contact_opens` (`pending → sent / failed / skipped`). Строка пишется как `pending` в транзакции открытия, задача ставится после коммита. Плюс ретраи и sweep в beat для зависших `pending`. | Как с эмбеддингами: если брокер лежал в момент открытия, уведомление не теряется молча. | Ретраи без sweep. Fire-and-forget. |
| 16 | **Исходы отправки.** Ответ `200` → `sent`. Telegram `403` (бот заблокирован или не было `/start`) и любой другой `4xx`, включая `400 chat not found` → `failed` без ретраев и без флага на `users`. `429`, `5xx`, таймаут → ретрай. Ретраи исчерпаны → `failed` окончательно: sweep `failed` не трогает. | Повтор `4xx` не поможет. Уведомление, опоздавшее на часы, ценности не имеет. | Флаг `bot_blocked` на `users`. Скрывать таких людей из ленты. Sweep по `failed`. |
| 17 | **Перепроверка при отправке.** Воркер читает текущие данные: открывший забанен, без username или `deleted_at`, либо владелец забанен или `deleted_at` → `skipped`. Пауза отправке не мешает. Удалённый профиль — строки уже нет (каскад). `pending` старше 24 ч → `skipped`. | Задача может уйти поздно (ретраи, sweep). После долгого сбоя пачка старых уведомлений разом выглядела бы как спам. | Отправлять как есть. Отправлять любое опоздавшее. |
| 18 | **Значения по умолчанию** (`NotificationConfig`): 5 попыток, backoff `5 с · 2^n` или `retry_after` от Telegram, если он больше; таймаут запроса 10 с; **sweep раз в 1 минуту** по `pending` старше 2 минут, пачка 100; порог устаревания 24 ч. | Уведомлению важнее прийти вовремя, чем вектору, поэтому sweep чаще, чем у эмбеддингов (10 мин / 5 мин). | Значения как у эмбеддингов. |

Текст уведомления:

```
Анна Иванова открыл(а) ваш контакт

Анкета: Tech & Product · Founder
Ваша анкета: Tech & Product · Engineering
Написать: @anna_founder

[ Открыть анкету ]
```

### Инфраструктура

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 19 | **Один `celery_app`, отдельная очередь и воркер.** `broker_url` переезжает из `EmbeddingConfig` в общий `WorkerConfig`: **`APP_CONFIG__EMBEDDING__BROKER_URL` → `APP_CONFIG__WORKER__BROKER_URL`**. Очередь `notifications`, контейнер `notification_worker`, один beat на оба sweep. | Медленный OpenAI не задерживает уведомления. Поле обязательное, поэтому забытый `.env` сразу даёт понятную ошибку старта, а не тихую поломку. **Предупредить второго разработчика:** ему нужно поправить `.env`. | Один воркер на обе очереди. Та же очередь `embeddings`. Оставить старое имя переменной. |
| 20 | **Провайдеры:** `stub` (пишет текст в лог, по умолчанию) и `telegram` (`httpx`, `sendMessage`, токен `BotConfig.token`, `chat_id = users.telegram_id` владельца). Реального бота пока нет, Telegram-провайдер проверяется на `httpx.MockTransport`. Prod fail-fast: `stub` запрещён, `mini_app_url` обязателен и должен быть `https`. | Как с эмбеддингами: этап не блокируется отсутствием бота, провайдер меняется одной переменной окружения. | — |
| 21 | **Видимость в одном месте.** `_visible_to` переезжает из `sqlalchemy_feed.py` в `infrastructure/repositories/profile_visibility.py`. Её импортируют и лента, и `SqlAlchemyContactOpenRepository.get_visible_contact(viewer, profile_id)`. Один SQL-запрос возвращает `username` владельца или `None`, а `404` решает use case. | Открытию нужно то же правило «кого можно показывать», но `get_cards` ленты `username` не отдаёт (и не должен). Это условие SQL над моделями, поэтому оно живёт в infrastructure: application о моделях не знает. Два запроса (видимость через `get_cards`, потом `username`) дали бы гонку между проверкой и чтением. | Метод в `IFeedRepository`. Application-сервис с двумя запросами. Копия условий в репозитории контактов. |

## 3. Технические детали без продуктового эффекта

Предложены исполнителем, отдельно не обсуждались; менять можно с записью в раздел 9.

1. **Таблица `contact_opens`:**
   - `opener_profile_id`, `opened_profile_id` — FK на `profiles.id`, оба `ON DELETE CASCADE`; первичный ключ — `(opener_profile_id, opened_profile_id)`;
   - `created_at`, `notification_status ENUM(pending|sent|failed|skipped)`, `notification_updated_at`.
2. **Индексы:**
   - префикс PK обслуживает поиск по `opener_profile_id`; окно лимита — join на `profiles.user_id`, за 24 часа строк мало;
   - btree `(opened_profile_id)` — для каскада и `is_contact_opened`;
   - partial `(notification_updated_at) WHERE notification_status = 'pending'` — для sweep.
3. **Вставка:** `INSERT ... ON CONFLICT DO NOTHING RETURNING`. `None` означает параллельный двойной клик, ответ как на повтор (`docs/rules.md`, «Concurrency»).
4. **Порядок в `OpenContactUseCase`:**
   1. `FeedViewerResolver.resolve`;
   2. `get_visible_contact`;
   3. существующая пара → повтор;
   4. `lock_for_update`;
   5. подсчёт и самое старое открытие в окне;
   6. `429` или insert;
   7. `commit`;
   8. enqueue (никогда не бросает, как `CeleryEmbeddingJobQueue`).
5. **Текст уведомления** собирает чистый domain-сервис `domain/services/contact_notification_text.py` из сущности `ContactNotification`.
6. **Роутер** — `routers/contacts.py` (правило именования модулей), путь `/profiles/{profile_id}/contact`, регистрация в `app/api/v1/__init__.py`.
7. **Ошибки Telegram:** провайдер переводит ответы в доменные исключения по образцу `openai_embedding.py`:
   - «недоступен» (`429`, `5xx`, таймаут, не-JSON) → ретрай;
   - «отклонено» (прочие `4xx`) → `failed`.

## 4. Файлы

Новые (образцы — эмбеддинги и лента):
- `app/domain/entities/contact.py`, `exceptions/contact.py` (`ContactLimitReachedError(limit, resets_at)`, `ContactProfileNotFoundError`), `enums/contact.py` (`NotificationStatus`)
- `app/domain/interfaces/{contact_open,notification_sender,notification_queue}.py`
- `app/domain/services/contact_notification_text.py`
- `app/application/use_cases/contact.py`: `OpenContactUseCase`, `SendContactNotificationUseCase`, `SweepStaleNotificationsUseCase`
- `app/infrastructure/models/contact_open.py` (+ экспорт в `models/__init__.py`), `mappers/contact_open_mapper.py`, `repositories/{sqlalchemy_contact_open,profile_visibility}.py`
- `app/infrastructure/providers/{stub_notification,telegram_notification}.py`
- `app/infrastructure/queues/celery_notification_queue.py`
- `app/api/v1/routers/contacts.py`, `schemas/contact.py`, `mappers/contact.py`
- `migrations/versions/..._contact_opens.py` (в `downgrade()` удалить enum)

Изменяемые:
- `app/core/config.py`: `WorkerConfig`, `ContactConfig`, `NotificationConfig` (plain `BaseModel`); перенос проверки «брокер не в DB кэша»; новые prod-проверки.
- `app/worker/celery_app.py`, `app/worker/tasks.py`: `send_contact_notification`, `sweep_stale_notifications` через `_run_with_container`.
- `app/core/composition/container.py`, `app/api/v1/__init__.py`.
- `app/domain/interfaces/user.py`, `app/infrastructure/repositories/user.py`: `lock_for_update`; фейк пользователя в `tests/fixtures`.
- `app/infrastructure/repositories/sqlalchemy_feed.py`: импорт видимости, `is_contact_opened` в `get_cards`. Плюс `domain/entities/feed.py`, `api/v1/schemas/feed.py`, `api/v1/mappers/feed.py`.
- `docker-compose.yaml` (`notification_worker`, beat), `.env.template`, `.env.test`.
- `CLAUDE.md`, `tests/fixtures/*` (фейки репозитория, отправителя, очереди).

## 5. Тесты

- **Unit:**
  - `OpenContactUseCase` на фейках: повтор бесплатен, в том числе при исчерпанном лимите; `remaining_today`; `resets_at`; `404` на свою, невидимую и ставшую невидимой анкету; порядок проверок;
  - текст уведомления с `last_name` и без него;
  - `SendContactNotificationUseCase`: `sent` / `failed` / `skipped`, все перепроверки, порог 24 ч;
  - sweep;
  - Settings в prod (stub, `mini_app_url`, брокер).
- **Integration:**
  - эндпоинт по HTTP: гейты, `201` / `200`, `429`, враждебный ввод id (вне диапазона, не число, своя анкета);
  - гонки `asyncio.gather`: двойной клик даёт одну строку; параллельные открытия разных анкет у границы лимита дают ровно `limit` строк;
  - `is_contact_opened` в ленте и в карточке;
  - задачи против реального Postgres и брокера;
  - брокер лежит → открытие всё равно `201`, строка `pending`;
  - миграция и индексы в тестах физической схемы.
- **Telegram-провайдер** на `httpx.MockTransport`: 200, 403, 400, 429 с `retry_after`, 5xx, таймаут, не-JSON.

## 6. Части

- **7a — открытие контакта.**
  - `WorkerConfig` (переименование переменной), `ContactConfig`;
  - вынос `_visible_to`, таблица и миграция, сущности и репозиторий, `lock_for_update`;
  - `OpenContactUseCase` без enqueue (строки остаются `pending`), эндпоинт, `is_contact_opened`;
  - тесты своей части.
- **7b — уведомления.**
  - `NotificationConfig`, порты, провайдеры, текст, очередь, задачи, sweep;
  - `notification_worker`, fail-fast, enqueue в `OpenContactUseCase`;
  - тесты своей части.
- **7c — документация и ручной сценарий.**
  - `CLAUDE.md`: Data model, API, карточка ленты, Celery и Redis, Risks (обход лимита удалением анкеты, дайджест, rate limiting этапа 8), Development stages;
  - раздел 9 этого документа.

## 7. Проверка (запускает человек)

После каждой части:

```bash
docker compose exec found_core_mini_app alembic upgrade head
docker compose exec found_core_mini_app alembic check
pytest -v --cov=app --cov-report=term-missing
```

Ручной сценарий (7c):
1. В `.env` заменить `APP_CONFIG__EMBEDDING__BROKER_URL` на `APP_CONFIG__WORKER__BROKER_URL`.
2. Пересобрать и поднять: `docker compose up -d --build`.
3. Залить сид: `python -m scripts.dev_seed_profiles`.
4. Сгенерировать `initData` под сид-пользователем (`scripts/dev_gen_init_data.py`), затем `POST /auth/telegram` и `GET /feed`.
5. Дважды `POST /profiles/{id}/contact`. Первый раз: `201`, `is_new_open: true`. Второй: `200`, `false`, `remaining_today` не изменился.
6. `docker compose logs notification_worker` показывает текст stub-уведомления.
7. `GET /feed` показывает у этой анкеты `is_contact_opened: true`.

## 8. Follow-up после этапа 7

- Истории: `GET /contacts/opened`, `GET /contacts/received`.
- Дайджест уведомлений, если владельцы популярных анкет начнут жаловаться на поток.
- Поминутный rate limiting открытий — вместе с общим rate limiting этапа 8.

## 9. Отклонения

Пока нет.
