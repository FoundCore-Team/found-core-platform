# Этап 8: moderation. Документ для исполнителя

Ветка: `feat/moderation`. Перед началом прочитать `CLAUDE.md` и `docs/rules.md`: они обязательны. Этот документ ничего не отменяет, он уточняет этап 8.

## 0. Как работать с этим документом

1. **Любое ключевое решение можно пересмотреть, но только осознанно.** Если выбираешь другой вариант или нашёл проблему, которой здесь нет, допиши в раздел 9 «Отклонения»: что изменил, почему, что отброшено.
2. Этап делается **четырьмя частями** (8a–8d, раздел 6) без PR между ними: техлид коммитит сам. Каждая часть заканчивается зелёными тестами и `alembic check`, `main` между частями не ломается. PR — в конце.
3. Если пункт плана оказался неверным, исправь план в том же коммите, а не только код.
4. Тесты и `alembic check` запускает человек и прикладывает результат. «Работает» без прогона не считается.
5. Коммиты: `feat(moderation): ...`, `test(moderation): ...`, `docs(moderation): ...`; без `Co-Authored-By` и других строк авторства (`docs/rules.md`, раздел 3, «Git»).

## 1. Контекст и цель

После этапа 7 в продукте есть всё, чем можно злоупотреблять, и нет ничего, чем можно этому ответить:
- жалоб нет; `is_banned` читается (`get_current_user`, `visible_to`), но выставить его нечем;
- статус `hidden_by_admin` проверяется во всех эндпоинтах владельца, но установить его нельзя;
- кастомные теги (`pending`) видны на чужих карточках, очереди модерации нет, слияния дублей (`Node.js` / `NodeJS`) нет;
- лимитов в минуту нет (контакт можно сжечь скриптом, ленту перебирать, карточки скрейпить): это вынесено в этап 8.5, `docs/plans/stage-8-5-rate-limiting.md`.

Что нужно:
- администраторы, назначаемые скриптом, и проверка `is_admin` на каждом запросе;
- журнал всех действий администратора (`admin_actions`);
- жалобы на анкеты и очередь жалоб для администратора;
- бан/разбан пользователя и скрытие/возврат анкеты;
- очередь кастомных тегов: одобрить, отклонить, слить.

Не входит: пользовательский rate limiting в Redis (этап 8.5, `docs/plans/stage-8-5-rate-limiting.md`), nginx-лимиты по IP (этап 10), аналитика (этап 9), любой фронтенд.

## 2. Ключевые решения и почему

Решения 1–34 утверждены пользователем. Помеченные `*` сначала выбрал исполнитель плана по поручению «реши сам»; пользователь просмотрел их все 2026-11-07 и утвердил (решение 10 изменено, см. раздел 9; решение 14 перенесено в этап 8.5). Решения 35–42 (rate limiting) перенесены в `docs/plans/stage-8-5-rate-limiting.md`; решения 9, 10 и 14 изменены, см. раздел 9.

### Доступ администратора и журнал

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 1 | **Администратор назначается скриптом** `python -m scripts.make_admin --telegram-id N [--revoke] --reason "..."`. Работает и в prod: это операционный инструмент, не сид. Пользователь должен хотя бы раз войти (иначе строки `users` нет). В журнал пишется строка с `actor_user_id = NULL` («система/скрипт»). Сессию скрипт берёт как `dev_seed_profiles`: `db_helper.session_factory()` и `Container(session, redis_client=redis_helper.client)`; Redis не используется, клиент ленивый. Проверки `run.env == prod` **нет**. | Админов единицы, назначать их нужно и в prod, без деплоя и без эндпоинта «стать админом» (его не от чего защищать). Use case `SetAdminUseCase` тот же, что и у остальных, скрипт только вызывает его и печатает результат. | Сид. Список `telegram_id` в `.env` (отзыв только деплоем). Эндпоинт назначения. |
| 2* | **`require_admin`** — зависимость поверх `get_current_user`; проверяет **живое** `user.is_admin` из строки, которую `get_current_user` читает на каждом запросе. В JWT admin-claim нет и не будет. Не админ → **`403 {"detail": {"code": "admin_required", "message": "Admin access required."}}`**. | `403` честнее: пользователь аутентифицирован, но прав нет. `404` ничего не скрыл бы: OpenAPI (`/openapi.json`) и так перечисляет `/admin/*`. Структурированный `detail` с `code` отличает его от `403` бана (там `detail` — строка с причиной) без разбора текста. Разжалованный админ теряет доступ на следующем запросе, а не после истечения токена. | `404` для не-админов. `403` без `code`. Проверка `is_admin` из токена. |
| 3 | **Журнал `admin_actions`**: каждый бан, разбан, скрытие, возврат, снятие жалоб (`dismiss`), одобрение/отклонение/слияние тега, выдача/отзыв админа пишет строку **в той же транзакции**, что и само действие (репозиторий журнала не коммитит, use case коммитит один раз). | Журнал не должен расходиться с состоянием: либо оба изменения, либо ни одного. Один коммит — правило `docs/rules.md`, «Transactions». | Журнал отдельной транзакцией или после коммита. Логи вместо таблицы. |
| 4 | **Цель действия — полиморфная пара** `(target_type ENUM(user\|profile\|tag), target_id BIGINT)` **без FK**. Дополнительно `details JSONB NOT NULL DEFAULT '{}'` для контекста действия (`owner_user_id`, число закрытых жалоб, `target_tag_id`). | Журнал — аудит, он должен пережить цель: анкету удаляет владелец, пользователя удалят (`DELETE /users/me` в планах). FK с `SET NULL` стёр бы «над кем», `CASCADE` стёр бы сам след. Три типа целей не оправдывают три nullable-FK и CHECK «ровно один не NULL». Целостность `target_id` гарантирует use case, который только что читал цель. | Три nullable-FK с CHECK. Одна колонка `target_id` без типа. |
| 5* | **`reason` обязателен для каждого действия, 1..1000 символов** после `strip`, без NUL. Для бана то же значение пишется в `users.ban_reason`, а значит **его видит забаненный пользователь** (`403 detail` из `get_current_user` и поле в ответе `/auth/telegram`). Это записывается в описание поля схемы: причину бана писать как текст для пользователя. | Решение пользователя: причина обязательна. Отдельной внутренней заметки не заводим — лишняя колонка. Риск «админ написал в причине лишнее» принят, перенесён в раздел 8. | Две причины (публичная и внутренняя). Необязательная причина. |
| 6* | **Списки админки — `limit`/`offset`**: `limit` 1..`moderation.page_size_max` (100), по умолчанию 50, `offset` 0..`moderation.max_offset` (100 000); ответ `{items, total, limit, offset}`. Порядок всегда с уникальным хвостом (раздел 3, пункт 6). | Списки небольшие, курсор (кодирование, проверка формы, ключ сессии) для них — лишний код. Журнал растёт без границы, но глубоко его никто не листает; если выйдет за ~100 тыс. строк — переход на keyset (раздел 8). | Keyset-курсор как в ленте. Без `total`. |

### Жалобы

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 7 | **Жаловаться можно только на анкету:** `POST /reports {profile_id, reason, comment?}`, `reason` ∈ `spam`, `fake`, `offensive`, `inappropriate_contact`, `other`; `comment` ≤ 1000. Анкета должна быть видна жалующемуся по `visible_to` (чужая, `active`, владелец не забанен, не удалён, с username), иначе `404` (то же, что своя или несуществующая). | Одно правило видимости на ленту, контакты и жалобы: жаловаться можно на то, что видел, и нельзя перебором id выяснять, какие анкеты есть. | Жалобы на пользователя, на тег. Отдельное правило видимости. |
| 8* | **У жалующегося нет требований к username и активной анкете.** Зависимость — `get_current_user` (бан и `token_version` уже внутри). `visible_to` принимает `viewer_user_id: int` вместо `FeedViewer` (использует только `viewer.user_id`; три вызова в ленте и контактах меняются на `viewer.user_id`). | Жалоба — предохранитель: человек, у которого все анкеты на паузе или нет username, мог видеть спам раньше. `FeedViewerResolver` потребовал бы активную анкету. Видимость остаётся той же, потому что условие одно. | `require_username` и `409 no_active_profile` как у ленты. Копия условий видимости. |
| 9 | **Одна открытая жалоба на пару `(reporter_user_id, profile_id)`:** частичный `UNIQUE (reporter_user_id, profile_id) WHERE status = 'open'` + `INSERT ... ON CONFLICT (reporter_user_id, profile_id) WHERE status = 'open' DO NOTHING RETURNING`. Открытой жалобы нет → `201` и новая строка; есть открытая → `200` с ней (причина и комментарий не перезаписываются), новой строки нет. После закрытия (`resolved`/`dismissed`) можно подать новую (`201`), старая остаётся для истории. Тест на `asyncio.gather`. | Правило `docs/rules.md`, «Concurrency»; повтор безопасен для двойного клика. Решение пользователя: пока жалоба открыта, повторять нельзя, после закрытия можно (анкету могли переписать). Риск: цикл «жалоба → `dismiss` → жалоба» до этапа 8.5 ничем не ограничен, кроме этого правила (раздел 8). | Одна жалоба на пару навсегда. Разрешение по `updated_at` анкеты или через N дней. Повтор — `409`. |
| 10 | **`reports.reporter_user_id` — `NOT NULL`, `ON DELETE CASCADE`**, `profile_id` — `ON DELETE CASCADE`. | Решение пользователя: жалоба удаляется вместе с аккаунтом жалобщика, «анонимных» строк нет. Жалобы на удалённую анкету не нужны (след действий админа остаётся в журнале). Риск принят: жалобщик может убрать свою жалобу из очереди, удалив аккаунт. | `SET NULL` (жалоба остаётся, автор `NULL`). |
| 11 | **Авто-скрытия по числу жалоб нет. Анкета из ленты жалующегося не пропадает.** | Решение пользователя: порог по числу жалоб — оружие для группы ботов; решает человек. | Скрытие после N жалоб. Скрытие у жалующегося. |
| 12* | **Статусы:** `open → resolved \| dismissed`. **Бан владельца** переводит в `resolved` все открытые жалобы на **все** его анкеты, **скрытие анкеты** — открытые жалобы на неё. Явный `POST /admin/reports/profiles/{profile_id}/dismiss` (причина обязательна, в журнал) переводит открытые жалобы на анкету в `dismissed` («нарушения нет»). Разбан и возврат анкеты жалобы не открывают. | Очередь отражает «что ещё надо рассмотреть». Дисмисс на уровне анкеты, потому что очередь сгруппирована по анкетам. Закрытые жалобы остаются для истории (решение 9). | Статус на уровне отдельной жалобы. Переоткрытие при разбане. |
| 13 | **Очередь жалоб для админа сгруппирована по анкетам:** `GET /admin/reports` → по анкете число открытых жалоб, разбивка по причинам, самая старая и самая новая; порядок `open_count DESC, oldest_at ASC, profile_id ASC`. `GET /admin/reports/profiles/{profile_id}` — анкета целиком, владелец и открытые жалобы (кто, причина, комментарий). Админ видит и `paused`, и `hidden_by_admin` анкеты. | Админу нужно «на кого больше всего и давно», а не поток отдельных строк. Видимость админа — не `visible_to`. | Плоский список жалоб. Видимость как у пользователя. |
| 14* | **Ограничения жалоб по частоте в этом этапе нет.** Суточный лимит `report_create` (10 в сутки на пользователя, повтор `200` тоже считается) делается в этапе 8.5 (там решение 36). | Rate limiting вынесен по решению пользователя. До 8.5 защита — только «одна открытая жалоба на пару» (решение 9). | Лимит через счёт в Postgres. |

### Бан

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 15 | **Бан бессрочный до ручного разбана**, `banned_until` нет. `ban`: `is_banned = true`, `ban_reason = reason`, `token_version + 1` (все выданные токены становятся `401`). Новый вход через `/auth/telegram` по-прежнему `200` с `is_banned` (фронтенд рисует экран бана), защищённые эндпоинты — `403` с причиной. Статусы анкет бан **не меняет**: они выпадают из ленты по `visible_to`. | `get_current_user` и `visible_to` уже умеют бан; не хватает только записи. Сессии ленты в Redis инвалидировать не нужно: `get_cards` проверяет видимость заново. | Срок бана. Менять статусы анкет при бане. Чистить сессии. |
| 16* | **Разбан: `is_banned = false`, `ban_reason = NULL`, `token_version` не меняется.** | Токен, выданный забаненному при входе, снова заработает без нового входа. Инвалидировать нечего. | Поднимать `token_version` и при разбане. |
| 17 | **Нельзя банить администратора, в том числе себя:** `409 {"code": "cannot_ban_admin"}`. Сначала отозвать админа скриптом. | Один админ не должен лишать доступа другого или блокировать себя; отзыв — операционное действие скрипта. | Разрешить банить других админов. |
| 18* | **Идемпотентность.** Бан забаненного, разбан не забаненного, скрытие скрытой, возврат нескрытой, `dismiss` без открытых жалоб, одобрение одобренного, отклонение отклонённого, повтор слияния `A → B` — **`200` с текущим состоянием, без записи, без строки журнала, без коммита**. Невозможный переход (отклонить слитый, слить в слитый) — `409`. Причина первого бана не затирается. | Повтор того же действия — не ошибка (двойной клик, ретрай, два админа), журнал фиксирует только изменения. Тот же приём, что у `pause`/`resume` (этап 4). | `409` на повтор. Журнал на каждый вызов. |
| 19 | **Атомарность перехода — условный `UPDATE ... WHERE <старое состояние> RETURNING`**, а `None` означает «кто-то уже сделал» (use case перечитывает и отвечает идемпотентно). Для бана условие включает `is_admin = false`, поэтому гонка «забанить» и «назначить админом» не нарушает инвариант. | Правило `docs/rules.md`: проверять в SQL, а не `SELECT`-ом перед записью. | Чтение, проверка и безусловная запись. |

### Скрытие анкеты

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 20 | **Скрытие** переводит `active`/`paused` → `hidden_by_admin`. Если это активная анкета владельца, указатель `users.active_profile_id` переносится как в `PauseProfileUseCase` (`get_newest_active_by_user_id`, `set_active_profile`). Открытые жалобы на анкету → `resolved`. Причина обязательна. | Скрытая анкета не может быть активной (`FeedViewerResolver` ответил бы `no_active_profile`). | Оставить указатель на скрытую анкету. |
| 21* | **Причины скрытия в `profiles` нет.** Владелец видит только `status = hidden_by_admin` в `/profiles/me`. Причина — в журнале. | Колонка потребовала бы миграции, схем и правил показа. Если нужна причина владельцу — это уведомление ботом (раздел 8). | Колонка `moderation_reason` в `profiles`. |
| 22 | **Возврат** (`unhide`): `hidden_by_admin → paused` **всегда**, указатель не трогаем; владелец возобновляет анкету сам (`resume`). | Анкета не появляется в ленте без ведома владельца. `ResumeProfileUseCase` уже ставит указатель, если его нет. | Возврат в `active`. Возврат в прежний статус. |
| 23 | **`DELETE /profiles/{id}` скрытой анкеты → `409`** (`ProfileHiddenError`), как остальные операции владельца. | Иначе владелец удаляет скрытую анкету и создаёт заново. Пока строка жива, `UNIQUE (user, category, role)` не даёт пересоздать. | Разрешить удаление. |
| 24* | **Уведомление о контакте пропускается (`skipped`), если анкета открывшего — `hidden_by_admin`.** Анкета владельца скрыта — не пропускаем. | Иначе нарушитель шлёт чужим людям своё имя, `@username` и ссылку на скрытую анкету через бота. Владельцу, чья анкета скрыта, знать о контакте можно (пауза тоже не останавливает). Поле `opener_profile_is_hidden` в `ContactNotificationContext`. | Не проверять. Пропускать при скрытой анкете владельца. |

### Теги

| # | Решение | Почему | Что отброшено |
|---|---|---|---|
| 25* | **Конкурентность модерации и сохранения анкеты.** Модерация берёт `SELECT ... FOR UPDATE` на строки тегов (в порядке id). Валидатор анкеты читает теги `FOR KEY SHARE` (`get_tags_in_scope_by_ids`), блокировка держится до коммита сохранения. `increment_tags_usage` переходит с `FOR UPDATE` на `FOR NO KEY UPDATE` (иначе два параллельных создания анкет с общим тегом гарантированно взаимоблокируются: оба держат `KEY SHARE`, оба ждут `FOR UPDATE`). Порядок блокировок везде: **теги → анкеты → `profile_embeddings`**. | Без этого `PUT` проверил тег `A`, слияние `A→B` закоммитилось, `PUT` вставил `A` в `profile_tags` — анкета осталась с алиасом. Теперь либо модерация ждёт сохранения (и увидит новую строку), либо валидатор ждёт модерацию, перечитывает строку и видит `merged` → `400`. `KEY SHARE` совместим с `KEY SHARE` и `NO KEY UPDATE`, поэтому сохранения анкет друг друга не сериализуют. | Блокировка без перечитывания. Повтор слияния «до чистого состояния». Жить с окном. |
| 26* | **Модерация тегов обновляет `profiles.updated_at` затронутых анкет** и переводит их `profile_embeddings` в `pending` с этим `updated_at` (`source_updated_at`). Явного enqueue нет: подхватывает sweep (`pending` старше 5 мин, раз в 10 мин, пачка 100). | Защита воркера (`save` при `source_updated_at == ожидаемому`) работает только если значение меняется: иначе воркер, прочитавший анкету до модерации, записал бы вектор по старому тексту и статус `ready`. Хэш входа меняется (в тексте эмбеддинга теги), поэтому свежий воркер пересчитает. Enqueue сотни анкет в запросе админа — сотни `send_task` с таймаутом, при лежащем брокере минуты. Побочный эффект: сессии ленты владельца пересоберутся — это правильно. | `source_updated_at = now()` без `updated_at`. Enqueue после коммита. |
| 27 | **Теги `pending` остаются видны на чужих карточках** (решение пользователя; принятый риск остаётся в `CLAUDE.md`). | Модерация постфактум: тег нужен автору сразу. | Показывать `pending` только автору. |
| 28 | **Очередь тегов:** `GET /admin/tags/pending`, старые первыми (`created_at, id`); у тега: создатель, `usage_count`, области (`scopes`), число анкет с тегом. Для этого `tags.created_at` (`server_default now()`, существующие строки получают время миграции) и частичный индекс `(created_at, id) WHERE status = 'pending'`. | Очередь идёт по возрасту, а `pending` — малая часть тегов: индекс маленький. | `created_at` без индекса. Очередь по `id`. |
| 29 | **`approve`**: `pending → approved`. **`reject`**: `pending \| approved → rejected`, удаляет строки `profile_tags` тега и помечает эмбеддинги затронутых анкет. Тег остаётся `rejected`, поэтому повторный ввод даёт `409 TagRejectedError`. У анкеты может остаться меньше 5 тегов (минимум проверяется только при сохранении) — принято. Других действий (переименование, возврат из `rejected`, создание тегов админом) нет. | Решение пользователя. | Снимать анкету при падении ниже минимума. Возврат из `rejected`. |
| 30 | **Слияние `A → B`.** `A` остаётся алиасом: статус `merged`, `merged_into_tag_id = B`. Цель `B` — `approved` и не алиас; `A != B`; `A` — `pending` или `approved`. `profile_tags` переносятся `INSERT ... SELECT ... ON CONFLICT DO NOTHING` + `DELETE`, `tag_scopes` копируются так же. | Решение пользователя. Копирование областей обязательно: валидатор требует тег в области категории/роли анкеты, без области `B` следующий `PUT` перенесённой анкеты дал бы `400`. | Удалять `A`. |
| 31* | **Детали слияния.** (а) `B.usage_count += A.usage_count`; (б) алиасы, ссылавшиеся на `A`, переадресуются на `B` (цепочек нет — глубина всегда 1); (в) у `A` остаётся её `usage_count`; (г) `CHECK ((status = 'merged') = (merged_into_tag_id IS NOT NULL))`. | `suggest` ранжирует по `usage_count`. Цепочки сделали бы каждый разбор алиаса циклом. | Не складывать счётчики. Цепочки алиасов. |
| 32 | **`POST /taxonomy/tags/custom` следует за алиасом:** найден тег `merged` → берём `B`, если `B` `rejected` → `409`, иначе область вызывающего добавляется **к `B`** и возвращается `B`. | Пользователь вводит `NodeJS` и получает `Node.js`; новые дубли не появляются. | Возвращать алиас. Ошибка на алиасе. |
| 33 | **Места, где важен статус тега:** `suggest` — `merged`/`rejected` отсекаются сами, **без изменений**; `CreateCustomTagUseCase` — см. 32; `ProfileContentValidator` — `rejected` → `409`, **`merged` → `ProfileTagInvalidError` (`400`)**: клиент с устаревшим id выбирает тег заново; `FeedFilterValidator` (`approved` only) — **без изменений**; кандидаты ленты, карточки, `FeedViewerResolver`, `_load_tags`, текст эмбеддинга — **без изменений**, потому что после модерации строк `profile_tags` для `rejected`/`merged` не остаётся (инвариант решения 25; в тестах — прямым SQL). | Инвариант делает остальные фильтры безвредными; добавлять их «на всякий случай» — прятать нарушение инварианта. | Расширить все фильтры. |
| 34 | **Кэш таксономии в Redis теги не хранит** (`categories`, `roles`, `role_fields`), инвалидировать нечего. | Проверено по `redis_taxonomy_cache.py`. | — |

### Rate limiting

Перенесён в этап 8.5: `docs/plans/stage-8-5-rate-limiting.md` (решения 35–42).

## 3. Технические детали без продуктового эффекта

Предложены исполнителем, отдельно не обсуждались; менять можно с записью в раздел 9.

### 3.1. Таблицы и миграции

**`admin_actions`** (миграция 8a, `down_revision = 'b3f8c2a91d47'`). `downgrade()` удаляет таблицу и **явно** `DROP TYPE admin_action_type`, `DROP TYPE admin_target_type`.

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | BIGINT PK | |
| `actor_user_id` | BIGINT NULL | FK `users.id` `ON DELETE SET NULL`; NULL = скрипт |
| `action` | ENUM `admin_action_type` | `ban_user`, `unban_user`, `hide_profile`, `unhide_profile`, `dismiss_reports`, `approve_tag`, `reject_tag`, `merge_tag`, `grant_admin`, `revoke_admin` |
| `target_type` | ENUM `admin_target_type` | `user`, `profile`, `tag` |
| `target_id` | BIGINT NOT NULL | без FK (решение 4) |
| `reason` | VARCHAR(1000) NOT NULL | |
| `details` | JSONB NOT NULL | `server_default '{}'` |
| `created_at` | TIMESTAMPTZ NOT NULL | ставит приложение |

Индексы: `ix_admin_actions_created_at_id (created_at, id)`, `ix_admin_actions_target (target_type, target_id)`.

**`reports`** (миграция 8b). Enum-типы `report_reason`, `report_status`, `downgrade()` их удаляет.

| Колонка | Тип | Примечание |
|---|---|---|
| `id` | BIGINT PK | |
| `reporter_user_id` | BIGINT NOT NULL | FK `users.id` `ON DELETE CASCADE` |
| `profile_id` | BIGINT NOT NULL | FK `profiles.id` `ON DELETE CASCADE` |
| `reason` | ENUM `report_reason` | `spam`, `fake`, `offensive`, `inappropriate_contact`, `other` |
| `comment` | VARCHAR(1000) NULL | |
| `status` | ENUM `report_status` | `open`, `resolved`, `dismissed`; `server_default 'open'` |
| `created_at` | TIMESTAMPTZ NOT NULL | ставит приложение |
| `resolved_at` | TIMESTAMPTZ NULL | |

Частичный `UNIQUE (reporter_user_id, profile_id) WHERE status = 'open'` (`uq_reports_open_per_reporter_profile`), `ix_reports_profile_id_status (profile_id, status)`. Закрытие по владельцу — `profile_id IN (SELECT id FROM profiles WHERE user_id = :u)` (индекс из `UNIQUE (user, category, role)`).

**`tags`** (миграция 8c):
- `created_at TIMESTAMPTZ NOT NULL DEFAULT now()`;
- `merged_into_tag_id BIGINT NULL`, FK `tags.id` (без `ondelete`), индекс `ix_tags_merged_into_tag_id`;
- `CHECK` `ck_tags_merged_target_matches_status`: `(status = 'merged') = (merged_into_tag_id IS NOT NULL)`;
- частичный индекс `ix_tags_pending_created_at (created_at, id) WHERE status = 'pending'`;
- `ALTER TYPE tag_status ADD VALUE 'merged'`.

**Порядок в `upgrade()` 8c:** новое значение enum нельзя использовать в той же транзакции, а `alembic upgrade head` идёт одной транзакцией. Поэтому:

```python
with op.get_context().autocommit_block():
    op.execute("ALTER TYPE tag_status ADD VALUE IF NOT EXISTS 'merged'")
# дальше: created_at, merged_into_tag_id, FK, индексы, CHECK
```

Если `autocommit_block` не заработает с асинхронным `env.py`, запасной вариант — `transaction_per_migration=True` в `context.configure` и две ревизии (значение, затем остальное). Проверяется прогоном `upgrade → downgrade -1 → upgrade`.

**`downgrade()` 8c:** удалить CHECK, оба индекса (частичный — **до** смены типа колонки), FK, `merged_into_tag_id`, `created_at`; `UPDATE tags SET status = 'rejected' WHERE status = 'merged'`; затем пересоздать тип:

```python
op.execute("ALTER TYPE tag_status RENAME TO tag_status_old")
op.execute("CREATE TYPE tag_status AS ENUM ('approved', 'pending', 'rejected')")
op.execute("ALTER TABLE tags ALTER COLUMN status TYPE tag_status USING status::text::tag_status")
op.execute("DROP TYPE tag_status_old")
```

Автогенерация — черновик, правится вручную; новые модели экспортируются из `models/__init__.py`; `alembic check` после каждой ревизии.

### 3.2. Эндпоинты

Все `/admin/*`: без токена `401`; не админ `403 {"code": "admin_required"}`; забаненный `403` с причиной. Тела — `extra="forbid"`; `reason` — `AdminReason` (`strip`, 1..1000, без NUL); id — `Int64Id` / `Path(ge=1, le=MAX_INT64)`. Не найдено — `404` со строкой; конфликты — `409 {code, message, ...}`.

| Метод и путь | Тело | Успех | Ошибки |
|---|---|---|---|
| `POST /admin/users/{user_id}/ban` | `{reason}` | `200 AdminUserSchema` | `404`; `409 cannot_ban_admin` |
| `POST /admin/users/{user_id}/unban` | `{reason}` | `200 AdminUserSchema` | `404` |
| `POST /admin/profiles/{profile_id}/hide` | `{reason}` | `200 AdminProfileSchema` | `404` |
| `POST /admin/profiles/{profile_id}/unhide` | `{reason}` | `200 AdminProfileSchema` | `404` |
| `GET /admin/reports?limit&offset` | | `200 {items: [ReportGroupSchema], total, limit, offset}` | `422` |
| `GET /admin/reports/profiles/{profile_id}` | | `200 {profile, owner, reports: [AdminReportSchema]}` | `404` |
| `POST /admin/reports/profiles/{profile_id}/dismiss` | `{reason}` | `200 {profile_id, dismissed_count}` | `404` |
| `GET /admin/tags/pending?limit&offset` | | `200 {items: [PendingTagSchema], total, limit, offset}` | `422` |
| `POST /admin/tags/{tag_id}/approve` | `{reason}` | `200 {tag: AdminTagSchema, affected_profiles}` | `404`; `409 tag_status_conflict` |
| `POST /admin/tags/{tag_id}/reject` | `{reason}` | то же | то же |
| `POST /admin/tags/{tag_id}/merge` | `{target_tag_id, reason}` | то же | `404`; `409 tag_status_conflict`; `409 merge_target_invalid` |
| `GET /admin/actions?limit&offset&target_type&target_id` | | `200 {items: [AdminActionSchema], total, limit, offset}` | `422` (`target_id` без `target_type`) |
| `POST /reports` | `{profile_id, reason, comment?}` | `201` новая / `200` уже есть открытая: `{id, profile_id, reason, comment, created_at}` | `404`; `422` |

Поля схем:
- `AdminUserSchema`: `id, telegram_id, first_name, last_name, username, is_admin, is_banned, ban_reason, created_at, last_active_at`.
- `AdminProfileSchema`: `id, user_id, category_id, role_id, status, country_code, timezone, bio, goals_description, extra_attributes, tags, created_at, updated_at`.
- `ReportGroupSchema`: `profile_id, profile_status, category_id, role_id, owner: {user_id, first_name, username, is_banned}, open_count, oldest_at, newest_at, reasons: {spam, fake, offensive, inappropriate_contact, other}` (все пять ключей всегда).
- `AdminReportSchema`: `id, reporter_user_id, reason, comment, created_at`.
- `PendingTagSchema`: `id, title, normalized_title, created_at, usage_count, profiles_count, creator: {user_id, first_name, username} (nullable), scopes: [{category_id, role_id}]`.
- `AdminTagSchema`: `id, title, normalized_title, status, merged_into_tag_id, usage_count`.
- `AdminActionSchema`: `id, actor_user_id (nullable), action, target_type, target_id, reason, details, created_at`.
- `merge_target_invalid.reason` ∈ `same_tag`, `target_not_found`, `target_not_approved`, `target_is_alias`.

Админские роутеры по областям (двое не конфликтуют в git): `admin_users.py`, `admin_profiles.py`, `admin_reports.py`, `admin_tags.py`, `admin_actions.py` (префиксы `/admin/...`, `tags=["Admin"]`); пользовательский — `reports.py`. Каждый админский роутер объявляет `dependencies=[Depends(require_admin)]` на уровне `APIRouter`; маршрут, которому нужен `id` админа, берёт `admin: UserEntity = Depends(require_admin)` (кэшируется). Тест обходит все маршруты `/api/v1/admin` и проверяет `403`.

### 3.3. Use cases и порядок проверок

Файлы `application/use_cases/`: `user_moderation.py` (`SetAdminUseCase`, `BanUserUseCase`, `UnbanUserUseCase`), `profile_moderation.py` (`HideProfileUseCase`, `UnhideProfileUseCase`), `report.py` (`CreateReportUseCase`, пользовательский), `report_moderation.py` (`ListReportGroupsUseCase`, `GetProfileReportsUseCase`, `DismissProfileReportsUseCase`, админские), `tag_moderation.py` (`ListPendingTagsUseCase`, `ApproveTagUseCase`, `RejectTagUseCase`, `MergeTagUseCase`), `admin_journal.py` (`ListAdminActionsUseCase`). Во всех мутациях: изменить состояние, закрыть жалобы / пометить эмбеддинги, **добавить строку журнала**, один `commit`.

- **`BanUserUseCase.execute(admin, user_id, reason)`**: `get_by_id` (нет или `deleted_at` → `UserNotFoundError`) → `is_admin` → `CannotBanAdminError` → уже забанен → вернуть → `set_ban`; `None` → перечитать (админ → `CannotBanAdminError`, забанен → вернуть, иначе 404) → `resolve_open_for_user` → журнал `ban_user` `{"resolved_reports": n}` → `commit`.
- **`UnbanUserUseCase`**: 404 → не забанен → вернуть → `clear_ban` → журнал → `commit`.
- **`SetAdminUseCase.execute(telegram_id, make_admin, reason) -> (UserEntity, bool)`**: `get_by_telegram_id` (нет / `deleted_at` → `UserNotFoundError`); выдача забаненному → `UserBannedError`; уже нужное состояние → `(user, False)`; `set_admin`; журнал с `actor = NULL`, `{"telegram_id": N}`; `commit`. Скрипт: код выхода 1 при ошибках, 0 при успехе и «ничего не изменилось».
- **`HideProfileUseCase.execute(admin, profile_id, reason)`**: `get_by_id` → 404 → уже скрыта → вернуть → `hide` (условный `UPDATE ... WHERE status IN (active, paused)`); `False` → перечитать → перенос указателя как в `PauseProfileUseCase` → `close_open_for_profile(RESOLVED)` → журнал `{"owner_user_id", "previous_status", "resolved_reports"}` → `commit`.
- **`UnhideProfileUseCase`**: 404 → не скрыта → вернуть → `unhide` (`hidden → paused`) → журнал → `commit`.
- **`CreateReportUseCase.execute(user, profile_id, reason, comment)`**: `is_profile_visible_to(user.id, profile_id)` → нет → `ReportProfileNotFoundError`; `create` (`ON CONFLICT (reporter_user_id, profile_id) WHERE status = 'open' DO NOTHING RETURNING`); `None` → `get_open_by_reporter_and_profile` → `(report, False)`; если открытая за это время закрыта (`None`) — одна повторная вставка; иначе `commit` → `(report, True)`.
- **`DismissProfileReportsUseCase`**: 404 → `close_open_for_profile(DISMISSED)`; `0` → вернуть без записи; иначе журнал → `commit`.
- **`ApproveTagUseCase`**: `lock_tags_for_update([id])` → 404 → `approved` → вернуть → не `pending` → `TagStatusConflictError` → `set_tag_status` → журнал → `commit`.
- **`RejectTagUseCase`**: lock → `rejected` → вернуть → `merged` → `TagStatusConflictError` → `detach_tag` (`DELETE ... RETURNING profile_id`) → `set_tag_status(REJECTED)` → `touch(profile_ids)` → `mark_pending_many` → журнал `{"previous_status", "detached_profiles"}` → `commit`.
- **`MergeTagUseCase.execute(admin, source_id, target_id, reason)`**: `same_tag` → `lock_tags_for_update([source, target])` (по возрастанию id) → источника нет → 404, цели нет → `target_not_found` → источник уже слит в эту цель → вернуть; слит в другую или `rejected` → `TagStatusConflictError` → цель `merged` → `target_is_alias`, не `approved` → `target_not_approved` → `copy_tag_scopes` → `replace_tag` → `merge_tag` (статус, ссылка, `usage_count`, переадресация алиасов) → `touch` → `mark_pending_many` → журнал `{"target_tag_id", "moved_profiles"}` → `commit`.
- **`CreateCustomTagUseCase`** (изменение): тег `MERGED` → подменить на цель; затем прежнее `REJECTED → TagRejectedError`; область добавляется к итоговому тегу.
- **`ProfileContentValidator`** (изменение): `merged` → `ProfileTagInvalidError`.
- **`DeleteProfileUseCase`** (изменение): `hidden_by_admin` → `ProfileHiddenError` (`409`).
- **`Pause/Resume`** (изменение): `set_status` возвращает `bool` (`UPDATE ... WHERE status <> 'hidden_by_admin'`); `False` → `ProfileHiddenError`. Иначе владелец, прочитавший `paused`, перезаписал бы только что поставленный `hidden_by_admin`.
- **`SendContactNotificationUseCase._should_skip`** (изменение): `or context.opener_profile_is_hidden`.

### 3.4. Репозитории: новые методы

- `IUserRepository`: `set_ban(user_id, reason) -> UserEntity | None` (поднимает `token_version`), `clear_ban(user_id)`, `set_admin(user_id, is_admin)` — условные `UPDATE ... RETURNING` с `populate_existing`; условия: бан — `is_banned = false AND is_admin = false AND deleted_at IS NULL`, разбан — `is_banned = true`, выдача — `is_admin = false AND is_banned = false`, отзыв — `is_admin = true`. Узкие методы, потому что `update()` пишет только поля Telegram.
- `IAdminActionRepository`: `add`, `list_actions(limit, offset, target_type, target_id)`, `count(target_type, target_id)`. Метод назван `list_actions`, а не `list`: имя `list` внутри класса затеняет встроенный тип в аннотациях.
- `IReportRepository`: `is_profile_visible_to`, `create -> ReportEntity | None`, `get_open_by_reporter_and_profile`, `list_open_groups(limit, offset)`, `count_open_groups`, `list_open_by_profile` (`created_at, id`), `close_open_for_profile(profile_id, status) -> int`, `resolve_open_for_user(user_id) -> int`. Группы — `GROUP BY profile_id` с join на анкету и владельца, разбивка по причинам — вторым запросом для id страницы.
- `IProfileRepository`: `set_status -> bool`, `hide -> bool`, `unhide -> bool`, `detach_tag(tag_id) -> list[int]`, `replace_tag(source, target) -> list[int]`, `touch(profile_ids) -> dict[int, datetime]`.
- `IProfileEmbeddingRepository`: `mark_pending_many(sources: dict[int, datetime])` — тот же upsert, что `mark_pending`, пачкой.
- `ITaxonomyRepository`: `lock_tags_for_update(tag_ids)`, `set_tag_status`, `merge_tag`, `copy_tag_scopes`, `list_pending_tags(limit, offset)`, `count_pending_tags`; `get_tags_in_scope_by_ids` читает `FOR KEY SHARE`; `increment_tags_usage` — `with_for_update(key_share=True)` (`FOR NO KEY UPDATE`). `TagEntity.merged_into_tag_id: int | None`.

### 3.5. Прочее

1. **Домен:** `entities/{moderation,report}.py`, `enums/{moderation,report}.py` (`AdminActionType`, `AdminTargetType`, `ReportReason`, `ReportStatus`), `exceptions/{moderation,report}.py`: `UserNotFoundError`, `CannotBanAdminError` (`moderation.py`), `ReportProfileNotFoundError` (`report.py`); новые в `exceptions/taxonomy.py`: `TagNotFoundError`, `TagStatusConflictError(status)`, `TagMergeTargetInvalidError(reason)`. `ADMIN_REASON_MAX_LENGTH = 1000`, `REPORT_COMMENT_MAX_LENGTH = 1000` в `domain/constants.py` (зеркалят размеры колонок).
2. **Конфиг (plain `BaseModel`):** `ModerationConfig(page_size_default=50, page_size_max=100, max_offset=100_000)`. В `Settings`: `moderation` с дефолтами. Секретов нет.
3. **`Container`:** `admin_action_repo()`, `report_repo()`, фабрики новых use case.
4. **`visible_to(viewer_user_id: int)`**; вызовы в `sqlalchemy_feed.py` (2) и `sqlalchemy_contact_open.py` (1) передают `viewer.user_id`.
5. **Порядок блокировок** `теги → анкеты → profile_embeddings` в модерации и в сохранении анкеты (валидатор берёт теги до `FOR UPDATE` анкеты в `update`).
6. **Порядок в списках:** жалобы — `open_count DESC, oldest_at ASC, profile_id ASC`; жалобы анкеты — `created_at, id`; очередь тегов — `created_at, id`; журнал — `created_at DESC, id DESC`; области тега — `category_id, role_id NULLS FIRST, id`.
7. **`AdminReason`** в `schemas/common.py`: `Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=ADMIN_REASON_MAX_LENGTH)]` + «нет NUL». Комментарий жалобы — тот же приём. Скрипт проверяет причину теми же константами.
8. _Перенесён в этап 8.5 (rate limiter)._
9. _Перенесён в этап 8.5 (фикстура `taxonomy_client` и счётчики)._
10. **TRUNCATE в `tests/integration/conftest.py`:** `reports`, `admin_actions` добавляются в список явно.
11. **Docstring `tests/fixtures/db_ops.py`** («ban endpoint is stage 8 work») обновляется.
12. **`increment_tags_usage` → `FOR NO KEY UPDATE` (обязательно, 8c).** Сегодня он берёт `FOR UPDATE`; вместе с `FOR KEY SHARE` в валидаторе два параллельных `POST /profiles` с общим тегом гарантированно взаимоблокируются. `FOR NO KEY UPDATE` совместим с `KEY SHARE`, между собой создания сериализуются в порядке id. Тест на `asyncio.gather`.
13. **Не меняется:** `VerifyAccessTokenUseCase` не проверяет `deleted_at` — путь мёртвый, пока нет удаления аккаунта; чинится вместе с `DELETE /users/me` (раздел 8).

## 4. Файлы

Новые:
- `app/domain/entities/{moderation,report}.py`, `enums/{moderation,report}.py`, `exceptions/{moderation,report}.py`
- `app/domain/interfaces/{admin_action,report}.py`
- `app/application/use_cases/{user_moderation,profile_moderation,report,report_moderation,tag_moderation,admin_journal}.py`
- `app/infrastructure/models/{admin_action,report}.py` (+ экспорт), `mappers/{admin_action_mapper,report_mapper}.py`, `repositories/{sqlalchemy_admin_action,sqlalchemy_report}.py`
- `app/api/v1/routers/{admin_users,admin_profiles,admin_reports,admin_tags,admin_actions,reports}.py`, `schemas/{moderation,report}.py`, `mappers/{moderation,report}.py`
- `scripts/make_admin.py`
- `migrations/versions/..._admin_actions.py`, `..._reports.py`, `..._tag_moderation.py`
- `tests/fixtures/{fake_admin_action_repository,fake_report_repository,moderation_data}.py`

Изменяемые:
- `app/core/config.py`, `app/core/composition/container.py`, `app/api/v1/__init__.py`, `app/api/v1/dependencies/auth.py` (`require_admin`)
- `app/domain/constants.py`, `entities/taxonomy.py`, `entities/contact.py`, `enums/taxonomy.py` (`MERGED`), `exceptions/taxonomy.py`, интерфейсы `user`, `profile`, `profile_embedding`, `taxonomy`
- `app/infrastructure/models/taxonomy.py`, `mappers/{taxonomy_mapper,contact_open_mapper}.py`, `repositories/{user,sqlalchemy_profile,sqlalchemy_profile_embedding,sqlalchemy_taxonomy,sqlalchemy_contact_open,sqlalchemy_feed,profile_visibility}.py`
- `app/application/use_cases/{taxonomy,profile,contact}.py`, `application/services/profile_content_validator.py`
- `app/api/v1/routers/{profiles,taxonomy}.py`
- `CLAUDE.md`, `docs/rules.md`
- `tests/integration/conftest.py`, `tests/fixtures/{fake_user_repository,fake_profile_repository,fake_taxonomy,fake_profile_embedding_repository,fake_contact_open_repository,db_ops}.py`

## 5. Тесты

### Unit (без Postgres и Redis)

- **Фейки** реализуют настоящие ABC, возвращают копии, пишут только те колонки, что пишет реальный репозиторий; `FakeUserRepository.set_ban` не банит админа и забаненного (возвращает `None`).
- **`SetAdminUseCase`:** выдача, отзыв, повтор (`False`, журнала нет), пользователя нет / удалён, выдача забаненному, `actor = None`, один `commit`.
- **`Ban/Unban`:** порядок проверок; бан админа и себя → `CannotBanAdminError`; повторы без записи и `commit`; причина первого бана не затирается; `token_version` растёт при бане и не меняется при разбане; бан закрывает жалобы на все анкеты владельца; журнал и состояние в одном `commit`.
- **`Hide/Unhide`:** перенос указателя на новейшую активную / `NULL`; скрытие `paused` не трогает указатель; закрытие жалоб; возврат всегда в `paused`; повторы идемпотентны; потерянная гонка условного `UPDATE`.
- **`Pause/Resume/Delete`:** скрытая анкета → `ProfileHiddenError`, в том числе когда `set_status` вернул `False`.
- **`CreateReportUseCase`:** своя / скрытая / паузная / забаненный владелец / без username → 404; `201`/`200`; потерянная гонка вставки; нет требований к username и активной анкете.
- **`Dismiss`:** нет открытых → `0`, нет журнала; есть → журнал и `commit`.
- **`Approve/Reject/Merge`:** все ветки решений 18, 29–31; переадресация алиасов; `usage_count`; копирование областей; порядок блокировки `[min, max]`; `touch` и `mark_pending_many` с одним `updated_at`; журнал.
- **`CreateCustomTagUseCase`:** алиас → цель + область добавлена к цели; цель `rejected` → `TagRejectedError`.
- **`ProfileContentValidator`:** `merged` → `ProfileTagInvalidError`, `rejected` → `TagRejectedError`.
- **`SendContactNotificationUseCase`:** `opener_profile_is_hidden` → `skipped`; скрытая анкета владельца не мешает.
- **`require_admin`** (живое `is_admin`, разжалованный между запросами — `403`).
- **`CreateReportUseCase`, повтор:** открытая жалоба есть → `(report, False)`; закрытая не мешает новой.

### Integration (реальные Postgres, Redis, HTTP)

- **Доступ:** обход всех маршрутов `/api/v1/admin` — без токена `401`, не админ `403 admin_required`, забаненный админ `403` с причиной, разжалованный после выдачи токена `403`.
- **`make_admin`:** выдача, отзыв, повтор, пользователя нет, забанен, пустая и длинная причина, **работает при `run.env = "prod"`**, журнал с `actor_user_id IS NULL`, новый админ сразу проходит `require_admin` старым токеном.
- **Бан по HTTP:** старый токен → `401`; новый вход → `200` с `is_banned`; защищённый эндпоинт → `403` с причиной; разбан → тот же токен работает; статусы анкет не меняются; забаненный выпадает из ленты, карточки, контактов; `409 cannot_ban_admin` (и себя); повтор → `200` без второй строки журнала.
- **Гонки (`asyncio.gather`):** (1) дубликаты `POST /reports` → одна открытая строка, один `201`; (2) параллельный `ban` ×N → одна строка журнала, один рост `token_version`; (3) `ban` ‖ `unban` → состояние согласовано с последней строкой журнала; (4) пачка `GET /feed` ‖ бан → только `200`/`401`/`403`; (5) `hide` ‖ `pause`/`resume` владельца → итог `hidden_by_admin`; (6) `hide` ×N → одна строка журнала; (7) `approve` ‖ `reject` → одно изменение; (8) **слияние ‖ `PUT /profiles/{id}` и ‖ `POST /profiles`**, в цикле: нет строк `profile_tags` на слитый тег; (9) слияние ‖ `reject` цели; (10) жалоба ‖ бан владельца → без `5xx`; (11) **параллельное создание анкет с общим тегом → без deadlock** (3.5, п. 12); (12) параллельные `POST /taxonomy/tags/custom` на алиас → один и тот же `B`.
- **Атомарность журнала:** падающий `IAdminActionRepository.add` на реальной БД → ни бана, ни закрытых жалоб, ни строки журнала.
- **Жалобы:** порядок групп с тай-брейкерами; разбивка со всеми пятью ключами; админ видит `paused`/`hidden_by_admin`; `dismiss` закрывает только открытые; бан закрывает жалобы на все анкеты владельца; скрытие — на неё; пока жалоба открыта, повтор → `200` без новой строки; после закрытия (`dismiss`, бан, скрытие) новая жалоба → `201`, старая остаётся; жалоба на невидимую анкету → `404`; после удаления жалобщика его жалобы исчезают вместе с ним (`CASCADE`).
- **Скрытие:** анкета исчезает из ленты, карточки, контактов (в том числе для уже открытой пары); указатель переносится; `/profiles/me` показывает `hidden_by_admin`; `PUT`/`pause`/`resume`/`activate`/**`DELETE`** → `409`; пересоздать нельзя; возврат → `paused` → `resume` → `active`; уведомление скрытого открывшего → `skipped`.
- **Теги:** очередь (порядок, поля, стабильная пагинация); `approve`/`reject`/`merge` по HTTP со всеми `409`; `reject` удаляет `profile_tags`, поднимает `updated_at`, ставит эмбеддинги `pending`; `POST tags/custom` и `PUT` с отклонённым → `409`; `merge` переносит анкеты (и с обоими тегами), копирует области (и категорийную), складывает `usage_count`, переадресует алиасы; `POST tags/custom` на слитый заголовок → `B`; `suggest` не отдаёт `merged`/`rejected`; `PUT` со слитым id → `400`; фильтр ленты со слитым → `invalid_filter`; прямым SQL — нет `profile_tags` на `rejected`/`merged`. **Воркер:** после `reject` вектор пересчитывается; воркер, начавший до модерации, теряет условную запись.
- **Физическая схема:** частичный `UNIQUE (reporter_user_id, profile_id) WHERE status = 'open'` (две открытые на пару нельзя, закрытая плюс открытая можно), все новые индексы, `CHECK` на `tags` в обе стороны, `tags.created_at` заполняется сам, `details` по умолчанию `{}`.
- **Враждебный ввод (`4xx`, не `5xx`):** id `0`, `-1`, `MAX_INT64 + 1`, не число; пустая, из пробелов, `1001` символ, с NUL, не строковая `reason`; неизвестная причина жалобы; комментарий `1001` и с NUL; лишнее поле; пропущенное поле; `limit` 0 и больше максимума; отрицательный и огромный `offset`; `target_id` без `target_type`; неизвестный `target_type`; повторяющиеся query-параметры; не-ASCII; `target_tag_id` равен самому тегу.

## 6. Части

- **8a — доступ администратора, журнал, бан. ✅ Сделано 2026-11-08, тесты зелёные.** Коммиты: `76ee7a5` config, `2039a4a` domain, `9801740` infrastructure + миграция `e5a1c7d93b28`, `d8abf62` методы `IUserRepository`, `e692fbd` use cases + скрипт, `228e81a` API, `2b9d877` тесты. План части: `ModerationConfig`; домен журнала, модель и миграция `admin_actions`; `require_admin`; `set_ban/clear_ban/set_admin` и фейк; `SetAdminUseCase` и `scripts/make_admin.py`; `Ban/UnbanUserUseCase` (без закрытия жалоб); роутеры `admin_users.py`, `admin_actions.py`; тесты части. Коммиты: config, domain, infrastructure+migration, use cases+script, api, tests.
- **8b — скрытие анкет и жалобы. ✅ Сделано 2026-11-08, целевые тесты зелёные.** Коммиты: `bf275b7` `visible_to(user_id)`, `cc664a8` домен жалоб, `272eda6` `reports` (модель, миграция `3f7852d69761`, репозиторий), `ab792d7` `hide`/`unhide`/`set_status -> bool`, `9df2562` use cases, `976ea90` бан закрывает жалобы, `7525a39` `opener_profile_is_hidden`, `9a9d5a2` `POST /reports`, `51dc9eb` админские эндпоинты, `3ba024c` и `4292f64` тесты. План части: `visible_to(viewer_user_id)`; `reports` (модель, миграция, репозиторий); `set_status -> bool`, `hide`, `unhide`; `Hide/Unhide`, защита `Pause/Resume`, `409` на `DELETE` скрытой; `opener_profile_is_hidden`; `POST /reports`, очередь жалоб, детали, `dismiss`; закрытие жалоб в `BanUserUseCase`; роутеры `reports.py`, `admin_profiles.py`, `admin_reports.py`; тесты части.
- **8c — модерация тегов. ✅ Сделано 2026-11-09, целевые тесты зелёные.** Коммиты: `ac2321a` домен (`MERGED`, исключения), `532966e` модель и миграция `a7c2e94f1b36`, `3819060` сущности и интерфейсы, `e9786e2` репозитории, `47db243` тесты репозиториев и фейки, `c5c6e7d` и `5eae02b` use cases, `fd6e1fb` unit-тесты, `4657ec2` API, `96c7102` и `4d6d89c` интеграционные тесты и гонки. План части: миграция `tag_moderation`; `TagStatus.MERGED`, `merged_into_tag_id`; новые методы репозиториев, `FOR KEY SHARE` в `get_tags_in_scope_by_ids`, `FOR NO KEY UPDATE` в `increment_tags_usage`; `ListPending/Approve/Reject/Merge`; алиас в `CreateCustomTagUseCase`; `merged` в валидаторе; роутер `admin_tags.py`; тесты части, включая гонки слияния и создания анкет.
- **8d — документация и ручной сценарий. ✅ Сделано 2026-11-09.** `CLAUDE.md` (Repository state, Data model, API, Moderation, файловая карта, `Container`, Acceptance criteria, Risks, Development stages) и `docs/rules.md` (журнал в той же транзакции, порядок блокировок, правила для тестов) обновлены; раздел 9 дополнен. Состав части по плану: `CLAUDE.md` (Repository state, Data model, API, Moderation, файловая карта, `Container`, Risks: добавить «причина бана видна пользователю» и «цикл жалоба → dismiss → жалоба до этапа 8.5», риски «Feed without rate limiting» и «Contact opens have only the daily limit» остаются до 8.5; в «Moderation, security, analytics» и «Follow-up» контактов заменить «stage 8» у rate limiting на «stage 8.5»; Development stages: этап 8 готов, новая строка 8.5 `rate-limiting`; Acceptance criteria); `docs/rules.md` (журнал в той же транзакции, порядок блокировок тегов); раздел 9.

### Статус на 2026-11-09: все четыре части сделаны, остаются полный прогон и PR

8c добавила модерацию тегов (очередь, `approve`/`reject`/`merge`, алиасы, обновление эмбеддингов, блокировки `FOR KEY SHARE` / `FOR NO KEY UPDATE`), 8d обновила документацию. Перед PR: один полный `pytest -v --cov=app --cov-report=term-missing` и `alembic upgrade head`, `alembic check`, `alembic downgrade -1`, `alembic upgrade head`, `alembic heads` (одна голова `a7c2e94f1b36`), затем ручной сценарий из раздела 7. Открыт один вопрос: TODO в `BanUserUseCase`. Ниже статус на момент окончания 8b.

### Статус на 2026-11-08 (вечер): 8a и 8b сделаны, следующий шаг 8c

8b добавила `reports`, `hide`/`unhide`, `POST /reports`, очередь жалоб, детали, `dismiss`, закрытие жалоб при бане и `opener_profile_is_hidden`; тесты зелёные (запускались по файлам, полный прогон делается один раз перед PR). Остаётся 8c (теги), затем 8d (документация и ручной сценарий). Ниже описание на момент окончания 8a.

- **8a сделана** (см. выше): `admin_actions` (модель, миграция, репозиторий), `require_admin`, `set_ban`/`clear_ban`/`set_admin`, `SetAdminUseCase`/`BanUserUseCase`/`UnbanUserUseCase`/`ListAdminActionsUseCase`, `scripts/make_admin.py`, эндпоинты `POST /admin/users/{id}/ban|unban` и `GET /admin/actions`. Unit и интеграционные тесты, включая гонки и атомарность журнала, зелёные; `alembic upgrade/check/downgrade -1` проходят.
- **8b сделана** (см. коммиты в списке частей выше). Пачки: (1) `visible_to(viewer_user_id)` и `reports` (домен, модель, миграция `3f7852d69761`, репозиторий); (2) `hide`/`unhide`/`set_status -> bool` и защита владельца; (3) use cases, бан закрывает жалобы (`resolved_reports` в `details`), `opener_profile_is_hidden`; (4) API; (5) тесты (unit, репозиторий и схема, эндпоинты, гонки, атомарность журнала). `alembic upgrade/check/downgrade -1` для миграции `reports` проходили; тесты запускались по файлам.
- **Осталось:** 8c (модерация тегов) и 8d (документация: полная файловая карта в `CLAUDE.md`, `docs/rules.md`, раздел 9, ручной сценарий, один полный прогон `pytest -v --cov=app` и `alembic upgrade/check/downgrade -1/upgrade/heads` перед PR). В `CLAUDE.md` статус 8a и 8b уже записан (разделы «Repository state», «Data model», «API», «Moderation, security, analytics», «Development stages»).
- **Открыто:** TODO в `BanUserUseCase` (что отвечать, если два других запроса забанили и разбанили пользователя между нашими чтениями: сейчас `404`; варианты: повторить запись или `409`).

## 7. Проверка (запускает человек)

После каждой части:

```bash
docker compose exec found_core_mini_app alembic upgrade head
docker compose exec found_core_mini_app alembic check
pytest -v --cov=app --cov-report=term-missing
```

Для 8a, 8b, 8c дополнительно:

```bash
docker compose exec found_core_mini_app alembic downgrade -1
docker compose exec found_core_mini_app alembic upgrade head
docker compose exec found_core_mini_app alembic heads    # одна голова
```

Для 8c перед `downgrade -1` создать слитый тег и убедиться, что после отката он `rejected`, а повторный `upgrade head` проходит.

Ручной сценарий (8d):
1. `docker compose up -d --build`, `alembic upgrade head`, `python -m scripts.dev_seed_taxonomy`, `python -m scripts.dev_seed_profiles`.
2. `initData` сид-пользователя (`scripts/dev_gen_init_data.py`), `POST /auth/telegram`; `docker compose exec found_core_mini_app python -m scripts.make_admin --telegram-id 1000000000001 --reason "dev admin"`; повтор печатает «ничего не изменилось»; `GET /admin/actions` с прежним токеном → `200`, строка с `actor_user_id: null`.
3. Второй сид-пользователь: `POST /reports` → `201`; повтор → `200`; на свою → `404`.
4. Админ: `GET /admin/reports` → группа `open_count: 1`; детали; `hide`; владелец `/profiles/me` → `hidden_by_admin`; `DELETE` → `409`; `unhide` → `paused`; `resume` → `active`.
5. Бан: старый токен → `401`; новый вход → `200`, `is_banned: true`; защищённый эндпоинт → `403` с причиной; `unban` → новый токен работает; бан админа → `409 cannot_ban_admin`.
6. Теги: `NodeJS` через `POST /taxonomy/tags/custom`; админ видит его в очереди, `merge` в `Node.js`; повтор `NodeJS` → `Node.js`. `reject` другого `pending` тега: у анкет он пропал; `docker compose logs embedding_worker` через несколько минут показывает пересчёт.
7. `GET /admin/actions` показывает все действия, новые первыми.

## 8. Follow-up после этапа 8

- Админский «почему рекомендовано» (`explain` для пары профилей).
- Уведомления ботом: о бане, скрытии, отклонённом теге; админам — о новых жалобах.
- Пользовательский rate limiting — этап 8.5 (`docs/plans/stage-8-5-rate-limiting.md`); скользящее окно — follow-up оттуда.
- Жалобы: суточный лимит `report_create` закрывает цикл «жалоба → `dismiss` → жалоба», пока его нет — следить вручную.
- Доработки профилей отдельными PR: username-гейт на категориях и `POST /profiles`, `terms_accepted_at`, `suggest` с тегами своих анкет.
- `DELETE /users/me`: не удалять физически забаненных (иначе бан обходится повторным входом), след в `admin_actions`, проверка `deleted_at` в `VerifyAccessTokenUseCase`.
- Списки пользователей и анкет в админке (поиск по `telegram_id`/`username`), `include_in_schema=False` для `/admin` при необходимости.
- Отдельная внутренняя заметка к бану (решение 5), keyset-пагинация журнала при росте, причина скрытия для владельца (решение 21).

## 9. Отклонения

1. **Rate limiting вынесен в этап 8.5** (решения 35–42, часть 8d, `RateLimitConfig`, `IRateLimiter`, `RateLimitGuard`, `rate_limited`, лимиты на эндпоинтах). Почему: решение пользователя, лимиты не связаны по коду с модерацией, отдельный PR проще читать. Что отброшено: один PR на весь этап 8. Текст перенесён в `docs/plans/stage-8-5-rate-limiting.md`.
2. **Решение 9:** вместо «одна жалоба на пару навсегда» — одна **открытая** жалоба на пару (частичный `UNIQUE ... WHERE status = 'open'`), после закрытия можно подать новую. Почему: решение пользователя (проще и позволяет пожаловаться на переписанную анкету). Что отброшено: «навсегда», разрешение по `updated_at` анкеты, через N дней. Риск описан в разделе 8.
3. **Решение 14:** суточный лимит жалоб теперь в этапе 8.5.
4. **8a, имя метода:** `IAdminActionRepository.list_actions` вместо `list` (имя `list` внутри класса затеняет встроенный тип в аннотациях). Уже отражено в 3.4.
5. **8a, расположение исключений:** `UserNotFoundError` и `CannotBanAdminError` лежат в `exceptions/moderation.py`, а не в `taxonomy.py` (модуль называется по содержимому). Уже отражено в 3.5.
6. **8a, `AdminRequiredError`:** добавлено исключение в `exceptions/moderation.py` для сообщения `403 admin_required` (в плане его не было; по образцу `UsernameRequiredError`).
7. **8a, `clear_ban` и `set_admin`** возвращают `UserEntity | None`, как `set_ban` (в 3.4 тип результата не был указан): use case получает новое состояние без повторного чтения.
8. **Решение 10:** вместо `SET NULL` по жалобщику — `NOT NULL` и `ON DELETE CASCADE`: жалоба удаляется вместе с аккаунтом автора. Почему: решение пользователя. Что отброшено: «анонимные» жалобы с `reporter_user_id = NULL`. Риск: жалобщик может убрать свою жалобу из очереди, удалив аккаунт.
9. **8b, расположение use cases жалоб:** админские `ListReportGroupsUseCase`, `GetProfileReportsUseCase`, `DismissProfileReportsUseCase` лежат в `report_moderation.py`, в `report.py` остался пользовательский `CreateReportUseCase`. Почему: один модуль не должен смешивать пользовательский и админский поток, `user_moderation.py` и `profile_moderation.py` уже разделены так. Уже отражено в 3.3 и разделе 4.
10. **8b, исключение «анкета не найдена» в админке:** `HideProfileUseCase`, `UnhideProfileUseCase`, `GetProfileReportsUseCase` и `DismissProfileReportsUseCase` бросают существующий `ProfileNotFoundError`, нового исключения не заводилось. Для жалобы пользователя остаётся отдельный `ReportProfileNotFoundError` (общее сообщение «Profile not found.»).
11. **8b, `CreateReportUseCase` и две попытки вставки:** цикл из двух попыток (отказ вставки → чтение открытой жалобы → если её успели закрыть, вторая вставка). После второго провала подряд бросается `RuntimeError` (для этого открытая жалоба должна дважды закрыться между двумя запросами, на практике недостижимо). Что отброшено: убрать цикл (тогда в этой гонке пользователь получил бы `500`).
12. **8b, `FakeReportRepository`:** `list_open_groups` и `count_open_groups` в фейке бросают `NotImplementedError` (очередь соединяет анкеты и пользователей, её проверяют на реальном Postgres в `test_report_repository.py`); остальной контракт фейк повторяет.
13. **8c, миграция одной ревизией:** `autocommit_block` работает с асинхронным `env.py` (`run_sync`), запасной вариант с двумя ревизиями и `transaction_per_migration=True` не понадобился. Проверено `upgrade → downgrade -1 (со слитым тегом: он стал rejected) → upgrade`, `alembic check` чист.
14. **8c, `get_tags_by_ids` читает с `populate_existing`:** `merge_tag` и `set_tag_status` пишут `UPDATE`-ами мимо сессии (`synchronize_session=False`), поэтому `MergeTagUseCase` перечитывает источник этим методом; без `populate_existing` вернулся бы устаревший объект из identity map. В плане этого не было.
15. **8c, типизированные причины и контекст в исключениях:** `TagMergeTargetProblem` (`StrEnum` в `domain/enums/taxonomy.py`) вместо строк для `merge_target_invalid.reason`; `TagStatusConflictError(status)` и `TagMergeTargetInvalidError(problem)` несут контекст для тела `409` (правило `docs/rules.md`: `__init__` может принимать контекст, `message` остаётся атрибутом класса).
16. **8c, сущности:** добавлены `PendingTagEntity`, `TagCreatorEntity`, `TagScopeEntity` (очередь не должна возвращать ORM-строки или `dict`) и `TagModerationResult` (в `entities/moderation.py`: ответ `approve`/`reject`/`merge` — тег и `affected_profiles`). В плане были только схемы.
17. **8c, `FakeTaxonomyRepository`:** `list_pending_tags` бросает `NotImplementedError` (очередь соединяет теги, пользователей, области и связи с анкетами; проверяется на Postgres в `test_tag_moderation_repository.py`), как фейк жалоб (пункт 12); `lock_tags_for_update` записывает полученный порядок id в `lock_calls`, чтобы тест проверил порядок `[min, max]`.
18. **8c, проверка силы тестов:** гонки проверены «мутациями»: без `FOR KEY SHARE` в валидаторе оба теста «слияние ‖ сохранение анкеты» падают, с `FOR UPDATE` вместо `NO KEY UPDATE` в `increment_tags_usage` тест на параллельное создание анкет ловит `DeadlockDetectedError`. Правило записано в `docs/rules.md`.
19. **Тестовая БД:** после изменения схемы `found_core_test_db` нужно пересоздавать (`create_all` не меняет существующие таблицы); записано в `CLAUDE.md` и `docs/rules.md`.
20. **Открыто:** TODO в `BanUserUseCase` (ответ `404`, если два других запроса забанили и разбанили пользователя между нашими чтениями) не решён; кандидаты: повторная запись или `409`.
