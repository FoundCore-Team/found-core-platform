# Этап 8.5: rate limiting. Документ для исполнителя

Ветка: `feat/rate-limiting`. Перед началом прочитать `CLAUDE.md` и `docs/rules.md`: они обязательны. Этот документ ничего не отменяет, он уточняет этап 8.5.

Этап вынесен из `docs/plans/stage-8-moderation.md` по решению пользователя (модерация и лимиты не связаны по коду, отдельный PR проще читать). Решения 35–42 ниже перенесены из плана этапа 8 **без изменений по существу**, номера сохранены, чтобы ссылки из истории не терялись. Решения 36, 38, 39, 40 (раньше со звёздочкой `*`, их выбирал исполнитель плана) утверждены пользователем 2026-11-11, звёздочки сняты. В тот же день пользователь утвердил добавки: `feed_card_daily` и `profile_write` в решении 36, имя поля `retry_after_seconds` в решении 41 и новые решения 43–45 (см. раздел 7).

**Зависит от этапа 8** (должен быть влит в `main`): лимит `report_create` вешается на `POST /reports`, а админские эндпоинты должны существовать, чтобы проверить, что лимиты их не касаются.

## 0. Как работать с этим документом

1. Любое ключевое решение можно пересмотреть, но только осознанно: допиши в раздел 7 «Отклонения», что изменил, почему и что отброшено.
2. Если пункт плана оказался неверным, исправь план в том же коммите, а не только код.
3. Тесты и `alembic check` запускает человек и прикладывает результат. «Работает» без прогона не считается. Миграций в этом этапе нет.
4. Коммиты: `feat(rate-limit): ...`, `test(rate-limit): ...`, `docs(rate-limit): ...`; без `Co-Authored-By` и других строк авторства (`docs/rules.md`, раздел 3, «Git»).

## 1. Контекст и цель

После этапа 8 в продукте есть жалобы и модерация, но нет лимитов в минуту:
- контакт можно сжечь скриптом за секунды (суточный лимит 20 есть, поминутного нет);
- ленту можно перебирать: каждая новая комбинация фильтров строит сессию (SQL по категории, последовательный скан векторов для третьего яруса) и хранит до 200 элементов в Redis 30 минут;
- `GET /feed/cards/{id}` отдаёт любую видимую анкету, перебор id скрейпит все;
- теги и автодополнение не ограничены;
- после этапа 8 жалобу можно подавать и закрывать по кругу: одна открытая жалоба на пару, но после закрытия можно подать новую. От массовых жалоб защищает только суточный лимит `report_create`.

Что нужно: пользовательский rate limiting в Redis с fail-open.

Не входит: nginx-лимиты по IP (этап 10), аналитика (этап 9), любой фронтенд.

## 2. Ключевые решения и почему

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 35 | **Фиксированное окно в Redis на пользователя и действие:** ключ `rate:{action}:{user_id}:{window_index}`, `window_index = int(time.time() // window_seconds)`; `INCR` и `EXPIRE ... NX` в одном `pipeline(transaction=True)`, ответ сравнивается с лимитом. Lua не нужен: `INCR` атомарен и сам возвращает новое значение. Счётчик растёт и на отклонённых запросах. | Один round-trip, ключи истекают сами. Всплеск до 2× на границе окна принят; скользящее окно (sorted set + Lua) — раздел 6. | Скользящее окно сразу. Счётчик только по успешным запросам. |
| 36 | **Лимиты по умолчанию** (`RateLimitConfig`, у каждого `{limit, window_seconds}`): `contact_open` 10/60 с (суточные 20 остаются в Postgres), `tag_create` 5/60 с и `tag_create_daily` 30/86400 с, `report_create` 10/86400 с, `tag_suggest` 60/60 с, `feed_request` 60/60 с, `feed_session_build` 10/60 с, `feed_card` 30/60 с и `feed_card_daily` 300/86400 с (антискрейпинг: одной минутной квоты хватило бы на ~43 000 карточек в сутки), `feed_view` 60/60 с, `profile_write` 5/600 с (создание и правка анкеты: каждое сохранение ставит пересчёт эмбеддинга, а настоящий OpenAI стоит денег; пять изменений за десять минут). Суточные окна по UTC (`epoch // 86400`). Значения положительные (валидатор). Меняются через `APP_CONFIG__RATE_LIMIT__<ACTION>__LIMIT` / `__WINDOW_SECONDS`. | Обычный человек ни в один не упирается. Контакт 10/мин не даёт сжечь 20 за секунды. У `tag_create` два лимита: суточный просил пользователь, всплеск режет минутный. `report_create` — единственная защита от цикла «жалоба → закрытие → новая жалоба» (этап 8, решение 9). | Единый лимит на всё. Сутки по таймзоне пользователя. |
| 37 | **Сбой Redis — fail-open:** любой `RedisError` → запрос разрешён, предупреждение в лог, флаг `_unavailable` на время запроса (как `redis_feed_session.py`). Логическая БД кэша (`redis_client` из `Container`). | Сбой Redis может ухудшить защиту, но не должен ронять API. | Fail-closed. Отдельная БД Redis. |
| 38 | **Механизм.** Порт `IRateLimiter` (`domain/interfaces`) возвращает **решение** `RateLimitDecision(allowed, retry_after_seconds)`, не бросает: репозиторий не принимает решений и не бросает доменных исключений. Реализация `RedisRateLimiter` (`infrastructure/repositories/redis_rate_limiter.py`). Решение превращает в исключение `RateLimitExceededError(action, retry_after)` сервис `RateLimitGuard` (`application/services/rate_limit_guard.py`): им пользуются двое — зависимость роутеров и `GetFeedUseCase`. | Один код «решение → исключение» вместо двух копий. | Исключение из самого лимитера. Два места с `if not allowed`. |
| 39 | **Применение двумя способами.** (а) Фабрика зависимостей `rate_limited(action)` в `api/v1/dependencies/rate_limit.py`: `dependencies=[Depends(rate_limited(RateLimitAction.TAG_SUGGEST))]`; внутри `Depends(get_current_user)` и `Depends(get_container)`. (б) `GetFeedUseCase` вызывает `RateLimitGuard.check(FEED_SESSION_BUILD, user.id)` **только при промахе сессии**, перед `_build_session`. Роутер ленты превращает `RateLimitExceededError` в `429` той же функцией, что и зависимость. | Тот же `Depends()` + `Container`, второго механизма DI нет. Только use case знает, был ли промах сессии. | Middleware. Лимит всей ленты только в use case. |
| 40 | **Порядок: аутентификация → лимит → `require_username` → проверки use case.** `get_current_user` кэшируется на запрос. Невалидный токен (`401`/`403`) счётчик не увеличивает. `409 username_required` / `no_active_profile` тоже тратят бюджет. | Лимит защищает инфраструктуру от наплыва аутентифицированных запросов, `429` не зависит от состояния профиля. | Лимит после `require_username`. |
| 41 | **Ответ:** `429 {"detail": {"code": "rate_limited", "message", "action", "retry_after_seconds"}}` (по `code` фронт отличает его от `contact_limit_reached`, суточного лимита контактов) и заголовок `Retry-After` (секунды до конца окна, ≥ 1). **Админы на пользовательских эндпоинтах не исключены.** Админских эндпоинтов лимиты не касаются. | Админ в роли пользователя — пользователь (украденный админский токен не даёт безлимитного скрейпинга). | Исключить админов. |
| 42 | **Лимиты по IP (nginx) — этап 10.** Скользящее окно — раздел 6. | Вне области этапа. | — |
| 43 | **`POST /auth/telegram` в этом этапе не ограничиваем:** лимит по IP делает nginx (этап 10). | До проверки `initData` у запроса нет `user_id`, ключ по пользователю не построить; подпись проверяется локально, ручка дешёвая. | Лимит по `telegram_id` после проверки подписи (отдельный путь кода и тесты). |
| 44 | **Закрываем TODO в `BanUserUseCase` в начале этапа:** если условный `UPDATE` вернул пусто, а перечитанный пользователь не забанен (между нашими чтениями другой админ забанил и разбанил), повторяем `UPDATE` один раз; пусто снова → `409` (код вроде `ban_state_changed`, исполнитель называет его в коде и записывает в раздел 7). Вместо нынешнего `404` для существующего пользователя. | Намерение админа выполняется в обычном случае, а гонка не маскируется под «пользователь не найден». | Сразу `409` (админ нажимает ещё раз). Цикл повторов (риск зависания). |
| 45 | **Повторное открытие контакта (`200`, бесплатный повтор) тоже считается в `contact_open`:** лимит стоит на роутере до use case и не знает, повтор это или нет. Двойной клик тратит 2 из 10 в минуту. | Проще и надёжнее: зависимость не лезет внутрь use case. | Считать только первое открытие (пришлось бы переносить проверку внутрь use case, после проверки пары). |

## 3. Технические детали без продуктового эффекта

Предложены исполнителем, отдельно не обсуждались; менять можно с записью в раздел 7.

1. **Домен:** `entities/rate_limit.py` (`RateLimitDecision`), `enums/rate_limit.py` (`RateLimitAction`), `exceptions/rate_limit.py` (`RateLimitExceededError(action, retry_after)`), порт `interfaces/rate_limiter.py` (`IRateLimiter.hit(action, user_id) -> RateLimitDecision`).
2. **Конфиг (plain `BaseModel`):** `RateLimitRule(limit, window_seconds)` с проверкой «положительные», `RateLimitConfig` — по полю на каждый член `RateLimitAction`. В `Settings`: `rate_limit` с дефолтами. Секретов нет.
3. **`Container`:** `rate_limiter()` (словарь `{action: (limit, window)}` из `settings.rate_limit`; тест: у каждого члена enum есть поле) и `rate_limit_guard()`; `get_feed_use_case()` получает guard.
4. **Rate limiter:** экземпляр на запрос; после первого `RedisError` — `_unavailable`. `retry_after = window_seconds - (now % window_seconds)`, не меньше 1. `pipeline(transaction=True)`: `incr`, `expire(key, window_seconds, nx=True)`.
5. **Тесты и Redis:** эндпоинты с лимитами используют фикстуру `taxonomy_client` (подменяет `get_container` клиентом Redis теста); в её докстринг — «для всех эндпоинтов, которые ходят в Redis». Счётчики не текут: `redis_client` делает `flushdb` перед каждым тестом.
6. **`GetFeedUseCase`:** `RateLimitGuard` в конструкторе; проверка `FEED_SESSION_BUILD` — после `_find_session`, до `_build_session`.
7. **Какие эндпоинты получают лимит:**

| Эндпоинт | Действие |
|---|---|
| `POST /profiles/{id}/contact` | `contact_open` (повтор `200` тоже считается, решение 45) |
| `POST /profiles`, `PUT /profiles/{id}` | `profile_write` (`pause`/`resume`/`activate`/`delete` без лимита: они не ставят пересчёт эмбеддинга) |
| `GET /taxonomy/tags/suggest` | `tag_suggest` |
| `POST /taxonomy/tags/custom` | `tag_create` и `tag_create_daily` |
| `POST /reports` | `report_create` (повтор `200` тоже считается) |
| `GET /feed` | `feed_request`, плюс `feed_session_build` при промахе сессии (в use case) |
| `GET /feed/cards/{id}` | `feed_card` и `feed_card_daily` |
| `POST /feed/views` | `feed_view` |

## 4. Файлы

Новые:
- `app/domain/entities/rate_limit.py`, `enums/rate_limit.py`, `exceptions/rate_limit.py`, `interfaces/rate_limiter.py`
- `app/application/services/rate_limit_guard.py`
- `app/infrastructure/repositories/redis_rate_limiter.py`
- `app/api/v1/dependencies/rate_limit.py`
- `tests/fixtures/fake_rate_limiter.py`

Изменяемые:
- `app/core/config.py`, `app/core/composition/container.py`
- `app/application/use_cases/feed.py`
- `app/api/v1/routers/{contacts,taxonomy,reports,feed,profiles}.py`
- решение 44 (TODO бана): `app/application/use_cases/user_moderation.py` (`BanUserUseCase`), `app/domain/exceptions/moderation.py` (новое исключение), `app/api/v1/routers/admin_users.py` (`409`), `tests/unit/test_user_moderation_use_cases.py`, `tests/integration/test_admin_races.py`
- `.env.template` (закомментированные примеры `APP_CONFIG__RATE_LIMIT__...`), `CLAUDE.md`, `docs/rules.md`
- `tests/integration/conftest.py` (докстринг `taxonomy_client`, если он там)

## 5. Тесты и проверка

### Unit (без Postgres и Redis)

- **`RateLimitGuard`**: решение `allowed=False` → `RateLimitExceededError` с `action` и `retry_after`.
- Зависимость **`rate_limited`**: `429`, тело, `Retry-After`.
- **`GetFeedUseCase`:** построение сессии вызывает `RateLimitGuard` один раз, попадание в сессию — ни разу; `RateLimitExceededError` не оставляет сессии в Redis.
- **`Settings`:** лимиты положительные, у каждого члена `RateLimitAction` есть поле, дефолты совпадают с решением 36 (включая `feed_card_daily` 300/86400 и `profile_write` 5/600).
- **`BanUserUseCase` (решение 44):** `UPDATE` вернул пусто, пользователь не забанен → второй `UPDATE`; снова пусто → `409`; «забанен кем-то другим» по-прежнему отвечает `200` с текущим состоянием.

### Integration (реальные Postgres, Redis, HTTP)

- **`RedisRateLimiter`:** граница лимита, ключ и TTL, смена окна (`freezegun`), независимость пользователей, **30 параллельных `hit` при лимите 10 → ровно 10 разрешённых**, Redis недоступен → разрешено, одно предупреждение, дальше без обращений.
- **Гонка бана (решение 44):** `asyncio.gather` бана и разбана одного пользователя против Postgres; результат не `404` и не `500`, итоговое состояние согласовано с журналом.
- **Каждый ограниченный эндпоинт по HTTP:** `N`-й `200`, `N+1`-й `429` с `Retry-After`; `tag_create` — оба лимита; лимит контактов не заменяет суточный; `feed_session_build` растёт только при промахе; невалидный токен не растит счётчики; админ на пользовательских эндпоинтах ограничен; админские маршруты не создают ключей `rate:*`; **при недоступном Redis эндпоинты отвечают как раньше**.

### Проверка (запускает человек)

```bash
pytest -v --cov=app --cov-report=term-missing
```

Ручной сценарий: 61 запрос `GET /taxonomy/tags/suggest` → последний `429` с `Retry-After`; `docker compose exec redis redis-cli -n 0 --scan --pattern 'rate:*'`; через минуту снова `200`; `docker compose stop redis` — запросы `200` (fail-open), в логе предупреждения.

## 6. Follow-up после этапа 8.5

- Скользящее окно rate limiting (sorted set + Lua).
- Лимиты по IP — этап 10 (nginx).

## 7. Отклонения

Отклонений от реализации пока нет. Решения, изменённые или добавленные при утверждении плана 2026-11-11:

1. Решения 36, 38, 39, 40 утверждены без изменений порядка и механизма (порядок «аутентификация → лимит → `require_username`», ключ `rate:{action}:{user_id}:{window_index}` с номером окна, счёт отклонённых запросов).
2. К решению 36 добавлены `feed_card_daily` 300/86400 с и `profile_write` 5/600 с (пять изменений за десять минут вместо «в минуту»: вектор всё равно считается не мгновенно). Риск: автосохранение анкеты на каждый шаг мастера на фронте упрётся в лимит; значение меняется через env.
3. В решении 41 поле тела `retry_after` называется `retry_after_seconds`.
4. Новые решения 43 (`/auth/telegram` оставлен nginx), 44 (TODO в `BanUserUseCase` закрывается в начале этапа) и 45 (повтор контакта считается в `contact_open`).
