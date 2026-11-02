# Этап 6: feed. Документ для исполнителя

Ветка: `feat/feed`. Перед началом прочитать `CLAUDE.md` и `docs/rules.md`: они обязательны. Этот документ ничего не отменяет, он уточняет этап 6.

## 0. Как работать с этим документом

Решения приняты по итогам обсуждения и проверки кода, но это не ТЗ «выполни и забудь».

1. **Любое ключевое решение можно пересмотреть, но только осознанно.** Если выбираешь другой вариант или нашёл проблему, которой здесь нет, допиши в раздел 12 «Отклонения»: что изменил, почему, что отброшено.
2. Этап делается **тремя частями** (6a, 6b, 6c, раздел 10) без PR: техлид коммитит сам. Каждая часть заканчивается зелёными тестами и `alembic check`, `main` между частями не ломается.
3. Если пункт плана оказался неверным, исправь план в том же коммите, а не только код.
4. Тесты и `alembic check` запускает человек и прикладывает результат. «Работает» без прогона не считается.

## 1. Контекст и цель

Лента — главный экран продукта. Пользователь листает анкеты, отсортированные относительно его **активного профиля**, в двух режимах:
- **Discovery**: без фильтров, только своя категория;
- **Search**: фильтры и каскад из трёх уровней, чтобы экран не был пустым.

Формула из `CLAUDE.md`:

```
score = (0.4 · cos_sim + 0.4 · tag_overlap + 0.2 · role_affinity) · activity_factor
tag_overlap     = |A∩B| / min(|A|, |B|)
role_affinity   = 1.0 та же роль · 0.5 другая роль той же категории · 0.0 другая категория
activity_factor = 1.0 (≤7 дней) · 0.8 (≤30 дней) · 0.5 (>30 дней)
```

До ленты нужен сид примерно на 100 профилей: без него ленту не оценить (критерий приёмки).

## 2. Ключевые решения и почему

**Кто что решил.**
- **Утверждены пользователем:** 1–14, 16, 17.
- **Изменены или добавлены по итогам ревью плана и утверждены пользователем:** 2, 3, 4, 10, 13, 18, 28–33 (что именно изменилось — раздел 12).
- **Предложены при проработке, отдельно не обсуждались:** 15, 19–27. Исполнитель вправе менять их с обоснованием в разделе 12.

| # | Решение | Почему | Что отброшено | Когда пересмотреть |
|---|---|---|---|---|
| 1 | **Search только в категории активного профиля.** Фильтра по категории нет. | `CLAUDE.md`: «активный профиль определяет фильтры». `role_affinity` для чужой категории равен 0, формула для межкатегорийного поиска не подходит. Хочешь искать в Edu & Growth — переключись на профиль оттуда. | Поиск в любой категории. | Если пользователи начнут массово заводить профили только ради поиска в другом блоке. |
| 2 | **Фильтры Search:** `role_id`, атрибуты роли (только `role_fields.is_filterable`), `tag_ids` (нужен хотя бы один из выбранных, только `approved`, решение 28), `country_code`, `same_timezone` (решение 3). Свободного текста нет. | Покрывает структурный поиск без синхронного вызова OpenAI и без новых индексов. | Свободный текст по bio (trigram-индекс или эмбеддинг запроса). | Когда понадобится текстовый поиск. |
| 3 | **Фильтр `same_timezone` (галочка «в моём часовом поясе»).** Совпадает, если **текущее** UTC-смещение зоны кандидата равно смещению зоны смотрящего: Москва, Киров и Стамбул совпадают. Use case через `TimezoneCatalog` находит все зоны с тем же смещением на момент построения сессии и передаёт список в SQL (`p.timezone = ANY(:zone_ids)`). То же правило, что у причины `same_timezone`. | Человеку важно созваниваться в одно время, а не жить в одной IANA-зоне. Считать смещение в Postgres нельзя: у него своя база таймзон, она может разойтись с `tzdata`, и фильтр начнёт противоречить причине на карточке. | Точное совпадение IANA-зоны (Москва и Киров «разные»). Фильтр «±N часов». Выбор произвольной зоны. | Если попросят «±N часов». |
| 4 | **Tier 2 (`PARTIAL_MATCH`) — совпал хотя бы один критерий фильтра, но не все.** Критерии: роль, каждый атрибут отдельно, теги (один критерий: есть хотя бы один тег фильтра), страна, таймзона. Внутри tier 2 сортировка: **число совпавших критериев DESC**, потом `score DESC`, потом `profile_id ASC`. Незаполненный атрибут кандидата — не совпал. Кандидат с 0 совпадений в Search виден только через tier 3. | Фильтры — прямое указание пользователя, score — наша догадка, поэтому в Search фильтры главнее. Пример: при `engineering + senior + Go` инженер-сеньор без Go (2 из 3) стоит выше продакта с Go (1 из 3), даже если у продакта score выше. Порядок совпадает с галочками причины `matched_filters`, поэтому выдача выглядит логичной. | Tier 2 только по тегам фильтра (было: инженер-сеньор без Go выпадал из Search совсем). Только `score` внутри tier 2. Веса критериев. | Если на сиде окажется, что какой-то критерий (например, роль) важнее остальных: тогда веса критериев. |
| 5 | **Tier 3 (`RECOMMENDED_BY_BIO`) ищет во всех категориях**: кандидаты с валидным вектором и `cos_sim ≥ feed.bio_tier_min_cos_sim`, которые не попали в tier 1–2. | Решение пользователя: последний уровень — чистая семантика, он может найти человека из соседнего блока. Порог нужен, иначе tier 3 включает всех подряд. | Только своя категория. | После калибровки порога на OpenAI (раздел 9). |
| 6 | **Сырые сигналы считает SQL, формулу считает Python.** Один SQL-запрос отбирает кандидатов и отдаёт `cos_sim`, теги, роль, `last_active_at`, флаги tier. Формула, веса, деградация, причины и каскад — в чистых domain-сервисах `FeedScorer` и `FeedRanker`. | Формула покрывается unit-тестами без БД (критерий приёмки), логика не размазана по двум слоям. При сотнях и тысячах кандидатов на категорию это дёшево. | Формула целиком в SQL (`ORDER BY score LIMIT 200`). | Когда в категории станет больше нескольких тысяч активных анкет: тогда нужен предфильтр или перенос формулы в SQL. |
| 7 | **Деградация по паре, пропорционально.** Если у пары «смотрящий — кандидат» нет двух сравнимых векторов, веса становятся `tags 0.4/0.6`, `role 0.2/0.6` (≈0.667 и 0.333). | Сохраняется пропорция tags:role из формулы. Без вектора обычно сидят свежие анкеты, это минуты. | Поровну (0.6/0.4). Без перераспределения для кандидата. | Если анкеты без вектора начнут систематически обгонять анкеты с вектором (видно в сиде). |
| 8 | **Несколько профилей одного человека показываем все.** | Каждая анкета — отдельная роль и отдельное предложение, так задуман мультипрофиль. | Только лучший по score. | Если лента забьётся дублями. |
| 9 | **Публичная карточка:** `first_name`, `photo_url`, категория, роль, страна, таймзона, bio, goals, теги, атрибуты. **`username` и `last_name` скрыты.** | `username` — это и есть контакт. Он открывается на этапе 7 вместе с уведомлением владельца и лимитами. Если отдавать его в ленте, этап 7 теряет смысл. | Анонимная карточка. Карточка с username. | — |
| 10 | **Непрозрачный курсор**: смещение, хэш параметров и ключ сессии (решение 18). Хэш параметров обязан совпасть с текущим запросом, иначе 400 `invalid_cursor`. Пока сессия из курсора жива в Redis, страницы режутся из неё, даже если ключ текущей сессии уже другой (досчитался свой вектор, вышел деплой). Сессии нет (истекла, Redis лежит или перезапускался) — рейтинг строится заново по текущим входам и отдаётся срез с того же смещения. | Рейтинг детерминирован, поэтому пересчёт почти всегда даёт то же. Пользователь не теряет позицию, а фоновые события (вектор, деплой) не ломают листание. При лежащем Redis лента работает, только медленнее. | 409 `feed_session_expired`. Голый `offset`. Один отпечаток на всё (было: появление своего вектора посреди листания давало 400). Начинать с начала при истёкшей сессии. | Если дубли и пропуски после пересчёта станут заметны. |
| 11 | **Просмотр фиксирует отдельный `POST /feed/views`.** `GET`-запросы ничего не пишут. | Предзагрузка карточек и повторы сети не создают ложных просмотров. На этапе 9 это тот же хук для события «card open». | Запись просмотра внутри `GET /feed/cards/{id}`. | — |
| 12 | **Причины — код и параметры**: `{"code": "shared_tags", "tags": [{"id", "title"}]}`, `{"code": "same_role"}`. Текст собирает фронтенд. | Тот же подход, что с названиями стран: локализация по коду на клиенте. Компактно хранится в сессии. | Готовые русские строки. | — |
| 13 | **Username-gate для ленты делаем сейчас**: зависимость `require_username` поверх `get_current_user`, ответ 409 `username_required`. Стоит на **всех трёх** эндпоинтах ленты: `GET /feed`, `GET /feed/cards/{id}`, `POST /feed/views`. Повесить её на `/taxonomy/categories` и `POST /profiles` — задача follow-up PR. | Правило «без username ленту не смотришь» входит в этап 6. Отдельная зависимость позволит follow-up просто переиспользовать её. | Сначала все follow-up PR. Весь gate в этапе 6. | — |
| 14 | **Сид: JSON-фикстура с ~100 вручную написанными анкетами** на русском (`scripts/data/seed_profiles.json`). | С OpenAI сид даст настоящую семантику, и качество ленты можно будет оценить глазами. Шаблонные тексты дали бы плоскую семантику. | Шаблоны в коде. Смесь. | — |
| 15 | В фикстуре можно указать **новые теги**. Сид создаёт их со статусом `approved` и scope роли. | В сиде таксономии 8–10 тегов на роль. Если брать только их, все анкеты одной роли похожи, и `tag_overlap` почти всегда высокий. | Только существующие теги. | — |
| 16 | **Сид пишет в БД напрямую, затем синхронно считает эмбеддинги** через `EmbedProfileUseCase` с провайдером из `.env`. Каждая запись проверяется `ProfileContentValidator`. | Воркер и брокер не нужны. Пауза, бан, отсутствие username и даты активности задаются напрямую. Валидатор гарантирует, что сид не обходит правила анкет. | Через `CreateProfileUseCase` и очередь. | — |
| 17 | **Этап делается тремя частями без PR** (раздел 10). | Решение пользователя. | Один PR. Два PR. | — |
| 18 | **Сессия ленты в Redis** (тот же клиент и та же логическая БД, что кэш таксономии: 0 в dev, 1 в тестах), TTL 30 минут. Два хэша: **`params_hash`** = sha256(id активного профиля, режим, нормализованные фильтры) — то, что задал пользователь; **ключ сессии** = sha256(`params_hash`, `updated_at` активного профиля, флаг «у смотрящего есть валидный вектор», `FEED_ALGORITHM_VERSION`) — то, что меняется само. Redis-ключ `feed:session:{session_key}`. Сессия хранит своего владельца (`viewer_profile_id`, `params_hash`); сессия из курсора с чужим владельцем или другим `params_hash` считается промахом. | Новая сессия строится при смене фильтров, активного профиля, правке своей анкеты, появлении своего вектора и смене алгоритма: изменилось хоть что-то — другой ключ. Разделение на два хэша нужно для решения 10. Проверка владельца нужна потому, что ключ сессии приходит от клиента в курсоре: без неё подставленный чужой ключ открыл бы чужую сессию. | Явная инвалидация при правке профиля. Хранение сессии в Postgres. Один отпечаток на всё. | — |
| 19 | **Отдельный кэш-репозиторий** `IFeedSessionRepository` → `RedisFeedSessionRepository` по правилам «Caching» из `docs/rules.md`: типизирован на сущностях, ключи и сериализация внутри, `RedisError` даёт `None` или no-op, битое значение считается промахом. | Правила проекта. | Generic `ICacheRepository`. | — |
| 20 | **Косинус в Stage 6 считается точно**, HNSW-индекс в запросах ленты не используется. Discovery и tier 1–2 считают `cos_sim` для всех кандидатов категории. Tier 3 фильтрует `cos_sim ≥ порог` по всем активным анкетам (seq scan). | Финальная сортировка идёт по формуле, а не по расстоянию, так что `ORDER BY embedding <=> q` ничего не даёт. Есть ловушка: HNSW с `ORDER BY ... LIMIT 200` возвращает не больше `hnsw.ef_search` строк (по умолчанию 40), а фильтры (статус, бан, username) применяются **после** индекса, так что лента молча обрезалась бы. Индекс из этапа 5 остаётся. | Tier 3 через HNSW с `SET LOCAL hnsw.ef_search` или `hnsw.iterative_scan`. | При ~10 тыс. активных анкет, когда seq scan по векторам станет заметен (порядок ~100 мс). |
| 21 | **Вектор используется, только если `embedding IS NOT NULL` и `model` равна текущей** у обоих профилей пары (контракт этапа 5). Имя текущей модели `Container` вычисляет так же, как `embedding_provider()`, но **не создавая провайдера** (`"stub-v1"` для stub, `EmbeddingConfig.model` для openai). | Векторы stub и OpenAI несравнимы. Если создавать провайдера ради имени, `openai` без ключа уронит ленту в dev. | — | — |
| 22 | `cos_sim` обрезается снизу нулём: `max(0, 1 - distance)`. | Отрицательный косинус не должен уменьшать score ниже вклада тегов и роли. | Шкала [-1, 1]. | — |
| 23 | **Кто может попасть в чужую ленту:** `profiles.status = 'active'`, `NOT users.is_banned`, `users.username` не NULL и не пустой, `users.deleted_at IS NULL`, `profiles.user_id <> user_id смотрящего`. Исключаются **все** профили смотрящего, а не только активный. | Критерии приёмки (бан, username). Своя же вторая анкета в ленте бессмысленна. | Исключать только активный профиль. | — |
| 24 | **Порядок проверок смотрящего:** сначала `username_required`, потом `no_active_profile`. Если активный профиль по какой-то причине не `active` (например, hidden модерацией этапа 8), это тоже `no_active_profile`. Тело ошибки: `{"detail": {"code": "...", "message": "..."}}`. | Без username лента недоступна в принципе. Машиночитаемый `code` нужен фронтенду, чтобы выбрать экран. | — | — |
| 25 | **Гидрация страницы перепроверяет видимость.** Если кандидат из сессии за 30 минут встал на паузу, забанен, удалён или потерял username, он выпадает со страницы. На странице может оказаться меньше `limit` карточек при `has_more = true`. | Сессия хранит только id. Показывать скрытого человека нельзя. Перестраивать сессию из-за одного выпавшего — дорого. | Пересборка сессии. | — |
| 26 | Элементы ленты содержат **полные данные карточки**. `GET /feed/cards/{id}` нужен для диплинков (на этапе 7 ссылка из уведомления бота) и для обновления. Он отдаёт любую видимую анкету любой категории со score относительно активного профиля и `match_type = null`. | Страница из 20 анкет маленькая, лишний запрос на каждое открытие не нужен. | Превью в ленте и полная карточка отдельным запросом. | Если ответ ленты станет тяжёлым. |
| 27 | **`mode=search` без единого фильтра** даёт 422. | Без фильтров это Discovery. Пусть фронтенд переключает режим явно, иначе появятся две одинаковые сессии с разной семантикой tier. | Молча считать как Discovery. | — |
| 28 | **Статусы тегов.** В фильтре Search только `approved`: pending, rejected или несуществующий id → 400 `invalid_filter`. В карточках, в `tag_overlap` и в причине `shared_tags` — все теги анкеты кроме `rejected` (approved и pending, которые указал владелец). | Фильтр строится из общего справочника, туда немодерированное не идёт. Анкета — это слова самого человека, их показываем как есть. Rejected скрываем, чтобы решение модератора (этап 8) сразу действовало в ленте. Заодно отказ на неизвестные `tag_ids` не даёт мусорным id порождать новые сессии. | Показывать в карточках только approved. Молча игнорировать плохой тег в фильтре. | — |
| 29 | **Близость ролей не меняется**: та же роль 1.0, другая роль той же категории 0.5. | Discovery — это «похожие на меня люди», это осознанно, в том числе founder видит сверху других фаундеров. | Настраиваемые пары ролей в конфиге (founder↔engineering = 1.0). | После просмотра живой выдачи на сиде. |
| 30 | **Rate limiting в этапе 6 не делаем, это принятый риск.** (а) Каждый новый набор фильтров строит сессию заново (SQL по категории плюс seq scan векторов) и кладёт до 200 элементов в Redis на 30 минут, число сборок не ограничено. (б) `GET /feed/cards/{id}` отдаёт любую видимую анкету, перебором id можно выкачать все анкеты. | Решение пользователя: закрыть вместе с общим rate limiting (лимит сборок сессий на пользователя в минуту, лимит на `cards` и `views`). Частично смягчено решением 28. | Свой лимит в этапе 6. | При появлении rate limiting — закрыть обязательно. |
| 31 | **Tier 3 не запрашивается, если tier 1+2 уже набрали `session_cap`.** | Bio-запрос — это seq scan по всем категориям, а его результат всё равно не попал бы в сессию. | Всегда делать три запроса. | — |
| 32 | **Сид защищён от prod.** Скрипт (и с `--reset`, и без) отказывается работать при `run.env == prod`. Username сид-пользователей с префиксом `seed_` (`seed_anna_backend`). | `--reset` удаляет пользователей по диапазону `telegram_id`, а id в Telegram могут занимать до 52 бит. Реалистичный username может принадлежать живому человеку: на стенде с тестировщиками этап 7 отправил бы их писать постороннему. | Только запрет в prod с реалистичными username. | — |
| 33 | **Причина `matched_filters`** (только Search, tier 1 и 2): `{"code": "matched_filters", "matched": ["role", "attr:grade"], "missed": ["tags"]}`. Коды критериев: `role`, `attr:<key>`, `tags`, `country`, `timezone`. | Пользователь видит, почему человек стоит в выдаче («engineering ✓, senior ✓, Go ✗»), и порядок tier 2 совпадает с этими галочками. | Без причины. | — |

## 3. Технические факты, которые нужно учесть

1. **`register_vector` не добавлять** (см. `CLAUDE.md`, «Tech stack»). Расстояние считать в SQL: `pe.embedding <=> v.embedding`. Сам вектор в Python не тащить.
2. **`ProfileEmbeddingModel.embedding` — `deferred=True`.** В запросах ленты вектор не нужен совсем: косинус считается в SQL. Нужны только `input_hash` и `model`, их достаточно, чтобы понять, есть ли вектор (`input_hash` заполнен тогда и только тогда, когда вектор есть).
3. **Stub-векторы почти ортогональны** (cos ≈ 0 ± 0.03). С stub семантическая часть почти нулевая, но вес **не** перераспределяется: векторы формально есть. Значит, в dev `match_score` обычно не выше ~60, а tier 3 пуст (порог не пройден). Это ожидаемо. Каскад tier 3 в тестах проверяется на сконструированных векторах.
4. **`users.last_active_at` обновляется только в `POST /auth/telegram`.** Мини-апп вызывает его при каждом открытии и после истечения JWT, поэтому для гранулярности 7/30 дней этого достаточно. Индекс по `last_active_at` из `CLAUDE.md` не нужен: колонка читается для кандидатов, а не фильтруется.
5. **Логин под сид-пользователем перезапишет его профиль Telegram.** `AuthenticateTelegramUserUseCase` берёт `username` и `first_name` из `initData`, а `scripts/dev_gen_init_data.py` зашивает `testuser` и `Test`. Нужны флаги `--username`, `--first-name`, `--no-username`.
6. **Смещение таймзоны** считается на момент построения сессии (`TimezoneCatalog.utc_offset`). Причина `same_timezone` и фильтр `same_timezone` означают равное текущее смещение, а не равную IANA-зону. В `TimezoneCatalog` нужен метод «все зоны каталога с тем же текущим смещением, что у данной» (сейчас есть только `zone_ids_for(country)`). Список зон в ключ сессии не входит: он выводится из зоны смотрящего, а смещение меняется дважды в год вместе с летним временем, и новая сессия на следующем запросе это подхватит.

## 4. Архитектура

```
GET /feed ─► require_username ─► GetFeedUseCase
                                   │ active profile? (IProfileRepository)
                                   │ filters valid?  (FeedFilterValidator: taxonomy + TimezoneCatalog)
                                   │ params_hash; cursor.params_hash ≠ params_hash → 400 invalid_cursor
                                   │ session_key = cursor.session_key or current key
                                   │ IFeedSessionRepository.get(session_key)  ── hit, owner ok ──┐
                                   │      miss / foreign owner / Redis down → current key        │
                                   │ IFeedRepository.list_*_candidates (SQL: signals)            │
                                   │ FeedRanker(FeedScorer) ─► top-200 ranked items              │
                                   │ IFeedSessionRepository.set                                  │
                                   ▼                                                             ▼
                              slice [offset : offset+limit] ─► IFeedRepository.get_cards (hydrate + is_viewed)
                                   ▼
                              FeedPage {items, next_cursor(offset, params_hash, session_key), has_more, end_reason}
```

Ранжирование не пишет в БД, поэтому лента — read-only use case без коммита. Пишет только `RecordProfileViewUseCase`.

### Таблица `profile_views`

| Колонка | Тип | Смысл |
|---|---|---|
| `viewer_profile_id` | BIGINT, FK `profiles.id` ON DELETE CASCADE | Активный профиль, под которым смотрели (`CLAUDE.md`: просмотры считаются по активному профилю). |
| `viewed_profile_id` | BIGINT, FK `profiles.id` ON DELETE CASCADE, индекс | Чью карточку открыли. Индекс нужен для каскадного удаления и будущей аналитики «кто меня смотрел». |
| `viewed_at` | timestamptz, NOT NULL | Первый просмотр. Повторный не обновляет. |

PK `(viewer_profile_id, viewed_profile_id)`, вставка `ON CONFLICT DO NOTHING`. Удаление аккаунта или профиля удаляет просмотры каскадом (`CLAUDE.md`, «Account deletion»).

### Каскад Search

Все кандидаты (кроме tier 3) лежат в категории смотрящего и проходят правило видимости (решение 23). **Критерии** фильтра: `role` (если задан `role_id`), `attr:<key>` для каждого атрибута (`extra_attributes @> {key: value}`), `tags` (если заданы `tag_ids`: есть хотя бы один), `country` (если задан `country_code`), `timezone` (если `same_timezone`: `p.timezone = ANY(:zone_ids)`). SQL возвращает список совпавших критериев. Каждый кандидат получает **один** tier, самый высокий из подходящих:

| Tier | Условие | Сортировка внутри |
|---|---|---|
| `EXACT_MATCH` | Совпали все заданные критерии. | `score DESC, profile_id ASC` |
| `PARTIAL_MATCH` | Совпал хотя бы один критерий, но не все (решение 4). | `число совпавших DESC, score DESC, profile_id ASC` |
| `RECOMMENDED_BY_BIO` | Любая категория, не попал в tier 1–2 (в том числе 0 совпадений в своей категории), у пары есть сравнимые векторы и `cos_sim ≥ bio_tier_min_cos_sim`. У смотрящего нет вектора — tier 3 пуст. Не запрашивается, если tier 1+2 уже набрали кап (решение 31). | `score DESC, profile_id ASC` |

Пример: `role=engineering, grade=senior, tag_ids=[Go]` (3 критерия). Инженер-сеньор с Go — tier 1. Инженер-мидл с Go и инженер-сеньор без Go (по 2 из 3) — верх tier 2. Продакт с Go и инженер-мидл без Go (по 1 из 3) — ниже в tier 2. Дизайнер без Go (0 из 3) — только tier 3, если текст похож. Если в фильтре один критерий, tier 2 пуст по определению и сразу идёт tier 3.

Уровни берутся по порядку, пока не набрано `session_cap` (200). Discovery — один уровень `DISCOVERY`: вся категория, `score DESC, profile_id ASC`, тот же кап.

### Пустые состояния

| Ситуация | Ответ |
|---|---|
| В сессии 0 элементов | `items: [], has_more: false, end_reason: "NO_CANDIDATES_YET"` |
| Последняя страница (смещение + limit ≥ длины сессии, длина > 0) | Элементы этой страницы, `has_more: false, end_reason: "END_OF_FEED"` |
| Курсор указывает за конец | `items: [], has_more: false, end_reason: "END_OF_FEED"` |
| Обычная страница | `has_more: true, end_reason: null, next_cursor` |

Ни один из этих случаев не ошибка (`CLAUDE.md`).

## 5. Реализация по слоям

### Domain
- `domain/constants.py`: `FEED_ALGORITHM_VERSION = 1`. Это константа, а не конфиг: версия меняется вместе с кодом формулы. Веса лежат в конфиге, их смена вступает в силу в пределах TTL сессии.
- `domain/enums/feed.py`: `FeedMode` (`discovery`, `search`), `FeedTier` (`DISCOVERY`, `EXACT_MATCH`, `PARTIAL_MATCH`, `RECOMMENDED_BY_BIO`), `FeedEndReason` (`NO_CANDIDATES_YET`, `END_OF_FEED`), `FeedReasonCode` (`matched_filters`, `shared_tags`, `same_role`, `same_country`, `same_timezone`, `similar_bio`, `recently_active`). Экспорт в `enums/__init__.py`.
- `domain/entities/feed.py`:
  - `FeedFilters` (`role_id: int | None`, `attributes: dict[str, str]`, `tag_ids: list[int]`, `country_code: str | None`, `same_timezone: bool`);
  - `FeedViewer` (профиль смотрящего, его `user_id`, `tag_ids` без rejected, флаг сравнимого вектора);
  - `FeedCandidate` (сигналы из SQL: `profile_id`, `category_id`, `role_id`, `tag_ids` без rejected, `cos_sim: float | None`, `last_active_at`, `country_code`, `timezone`, `matched_filters: list[str]` — коды совпавших критериев, решение 33; для Discovery и tier 3 пустой);
  - `FeedReason`, `FeedRankedItem` (`profile_id`, `tier`, `score`, `reasons`);
  - `FeedSession` (`viewer_profile_id`, `params_hash`, `items`, `built_at`);
  - `FeedCursor` (`offset`, `params_hash`, `session_key`): разобранный курсор, который API передаёт в use case;
  - `FeedCard` (`ProfileEntity`, `first_name`, `photo_url`, `is_viewed`);
  - `FeedPageItem` (`FeedCard`, `tier`, `match_score`, `reasons`), `FeedPage`.
- `domain/services/feed_scorer.py`: `FeedScorer` получает веса, коэффициенты роли, пороги активности и порог причины `similar_bio` в конструкторе (из `FeedConfig`). Метод `score(viewer, candidate, now, same_utc_offset) -> (float, list[FeedReason])`. Внутри: `tag_overlap`, `role_affinity`, `activity_factor`, обрезка cos, перераспределение по паре (решение 7). Причины в фиксированном порядке, `shared_tags` с не более чем `max_reason_tags` тегами по id.
- `domain/services/feed_ranker.py`: `FeedRanker` (принимает `FeedScorer` и `session_cap`) с методами `rank_discovery(...)` и `rank_search(...)`: присваивает tier по числу совпавших критериев относительно числа заданных (`FeedFilters`), убирает дубли между уровнями, сортирует (tier 2 — по числу совпавших, потом score, решение 4), добавляет причину `matched_filters` для tier 1–2, режет до капа. Отдельно сообщает, нужен ли bio-запрос (tier 1+2 меньше капа, решение 31). Чистая функция без БД.
- `domain/services/feed_session_fingerprint.py`: `build_feed_params_hash(viewer_profile_id, mode, filters) -> str` и `build_feed_session_key(params_hash, profile_updated_at, has_comparable_vector, algorithm_version) -> str` (решение 18; sha256 от нормализованных входов: отсортированные теги и атрибуты, `updated_at` в ISO-формате).
- `domain/exceptions/feed.py`: `NoActiveProfileError`, `UsernameRequiredError`, `InvalidFeedFilterError`, `InvalidFeedCursorError` (хэш параметров в курсоре не совпал), `FeedProfileNotFoundError`.
- `domain/interfaces/feed.py`, `IFeedRepository` (Postgres, read model, без коммитов):
  - `list_discovery_candidates(viewer, embedding_model) -> list[FeedCandidate]`;
  - `list_search_candidates(viewer, filters, timezone_ids, embedding_model) -> list[FeedCandidate]` (только категория смотрящего, кандидаты хотя бы с одним совпавшим критерием, совпавшие критерии считает SQL; `timezone_ids` — зоны с тем же смещением, пустой список при `same_timezone = false`);
  - `list_bio_candidates(viewer, exclude_profile_ids, min_cos_sim, embedding_model) -> list[FeedCandidate]` (все категории);
  - `get_candidate(viewer, profile_id, embedding_model) -> FeedCandidate | None` (для карточки);
  - `get_cards(viewer_profile_id, profile_ids) -> list[FeedCard]` (только видимые, в порядке входного списка, теги без rejected одним запросом, как в `SqlAlchemyProfileRepository._load_tags`).
- `domain/interfaces/profile_view.py`: `IProfileViewRepository.add(viewer_profile_id, viewed_profile_id, viewed_at) -> None`.
- `domain/interfaces/feed_session.py`: `IFeedSessionRepository.get(session_key) -> FeedSession | None`, `set(session_key, session) -> None`.

`IFeedRepository` читает несколько таблиц (`profiles`, `users`, `profile_tags`, `profile_embeddings`, `profile_views`). Это осознанно: это один read model ленты и один источник данных (Postgres), а не смешение Postgres и Redis.

### Application
- `services/feed_filter_validator.py`, `FeedFilterValidator` (зависит от `ITaxonomyRepository` и `TimezoneCatalog`, поэтому application, а не domain). Правила:
  - `role_id` должна принадлежать категории смотрящего;
  - атрибуты без `role_id` дают `InvalidFeedFilterError`;
  - ключ атрибута должен быть полем роли с `is_filterable`, значение — одной из `options`;
  - страна должна быть в каталоге (и в `allowed_country_codes`, если он задан);
  - каждый из `tag_ids` должен существовать и быть `approved`, иначе `InvalidFeedFilterError` (решение 28; нужен метод `ITaxonomyRepository` для чтения тегов по id без проверки scope).
- `use_cases/feed.py`:
  - `GetFeedUseCase.execute(user, mode, filters, cursor: FeedCursor | None, limit) -> FeedPage`:
    1. Активного профиля нет или он не `active` → `NoActiveProfileError`.
    2. Для search проверить фильтры.
    3. `params_hash`. Курсор есть и `cursor.params_hash` не совпал → `InvalidFeedCursorError`.
    4. Состояние вектора смотрящего (`IProfileEmbeddingRepository.get`), текущий ключ сессии.
    5. Курсор есть: `session_repo.get(cursor.session_key)`; сессия найдена и её `viewer_profile_id` и `params_hash` совпали — используем её (решение 10). Иначе (или без курсора): `session_repo.get(текущий ключ)`; при промахе собрать кандидатов (`timezone_ids` из `TimezoneCatalog`, bio-запрос только если нужен), вызвать `FeedRanker`, затем `session_repo.set`.
    6. Срез с `cursor.offset` (или 0), `get_cards`, собрать `FeedPage` с `match_score = round(score * 100)`. `next_cursor` несёт ключ той сессии, из которой реально резали.
  - `GetFeedCardUseCase.execute(user, profile_id) -> FeedPageItem` (`tier = None`). Невидимая анкета или своя → `FeedProfileNotFoundError` → 404.
  - `RecordProfileViewUseCase.execute(user, profile_id) -> None`: те же проверки смотрящего, анкета должна быть видима (`get_cards([id])` не пуст), затем `add` и коммит. Идемпотентно.
- Use cases для смещения таймзоны вызывают `TimezoneCatalog` и передают в `FeedScorer` готовый признак `same_utc_offset`, а в `list_search_candidates` готовый список `timezone_ids`, чтобы domain не зависел от `tzdata`.

### Infrastructure
- `models/profile_view.py` (`ProfileViewModel`, экспорт в `models/__init__.py`), миграция (`down_revision='7c1e5a9b3d42'`): таблица и индекс. `downgrade()` удаляет таблицу. Только схема.
- `repositories/sqlalchemy_feed.py`: SQL кандидатов. Набросок Discovery:

  ```sql
  WITH viewer AS (
      SELECT embedding FROM profile_embeddings
      WHERE profile_id = :viewer_profile_id AND embedding IS NOT NULL AND model = :model
  )
  SELECT p.id, p.category_id, p.role_id, p.country_code, p.timezone, u.last_active_at,
         CASE WHEN pe.embedding IS NOT NULL AND pe.model = :model
              THEN GREATEST(0, 1 - (pe.embedding <=> v.embedding)) END AS cos_sim,
         ARRAY(SELECT pt.tag_id FROM profile_tags pt WHERE pt.profile_id = p.id) AS tag_ids
  FROM profiles p
  JOIN users u ON u.id = p.user_id
  LEFT JOIN profile_embeddings pe ON pe.profile_id = p.id
  LEFT JOIN viewer v ON TRUE
  WHERE p.category_id = :category_id
    AND p.status = 'active'
    AND NOT u.is_banned
    AND u.username IS NOT NULL AND u.username <> ''
    AND u.deleted_at IS NULL
    AND p.user_id <> :viewer_user_id
  ```

  Если строки `viewer` нет, `v.embedding` равен NULL, и `cos_sim` тоже NULL — деградация происходит естественно. Теги во всех запросах ленты берутся без `rejected` (join на `tags`, решение 28). Search добавляет в `SELECT` совпавшие критерии (например, `array_remove(ARRAY[CASE WHEN p.role_id = :role_id THEN 'role' END, CASE WHEN p.extra_attributes @> :attr_grade THEN 'attr:grade' END, …], NULL)`, только для заданных фильтров) и в `WHERE` условие «совпал хотя бы один». Bio-tier убирает фильтр категории и добавляет `1 - (pe.embedding <=> v.embedding) >= :min_cos` и `p.id <> ALL(:exclude)`. Условие видимости вынести в одно место внутри репозитория: им же пользуется `get_cards`.
- `repositories/sqlalchemy_profile_view.py`: `INSERT ... ON CONFLICT DO NOTHING`.
- `repositories/redis_feed_session.py`: ключ `feed:session:{session_key}`. JSON вида `{"viewer_profile_id", "params_hash", "built_at", "items": [[profile_id, tier, score, reasons], ...]}`. Проверку владельца делает use case, репозиторий только возвращает сессию. TTL `feed.session_ttl_seconds` из конструктора. Обработка `RedisError` и битых значений — как в `RedisTaxonomyCacheRepository`, включая «после первой ошибки не ходить в Redis до конца запроса».

### API
- `api/v1/dependencies/auth.py`: `require_username(current_user = Depends(get_current_user)) -> UserEntity`. Нет username → 409 `{"detail": {"code": "username_required", ...}}`. Подключается ко всем трём эндпоинтам ленты (решение 13).
- `api/v1/schemas/feed.py`, `api/v1/mappers/feed.py` (сюда же кодирование и декодирование курсора: это HTTP-контракт), `api/v1/routers/feed.py`, подключение в `main.py`.
- `GET /api/v1/feed`:
  - параметры: `mode` (обязательный), `role_id`, `attr` (повторяемый, `key:value`, делится по **первому** двоеточию, не больше 10, ключ ≤ 64, значение ≤ 128), `tag_ids` (повторяемый, не больше 15, `1..MAX_INT64`), `country_code` (≤ 16), `same_timezone` (bool, по умолчанию `false`), `cursor` (≤ 512), `limit` (`1..feed.page_size_max`, по умолчанию `feed.page_size_default`);
  - курсор: url-safe base64 от `{"o": offset, "p": params_hash, "s": session_key}`. Битый base64, не-JSON, не тот тип, отрицательное или огромное смещение, хэши не 64 hex-символа → 400 `invalid_cursor`. `params_hash` не совпал с текущими параметрами → тоже 400 `invalid_cursor` (клиент сменил фильтры, но прислал старый курсор). Несовпадение ключа сессии ошибкой не считается (решение 10);
  - страница может содержать меньше `limit` элементов и даже 0 при `has_more = true` (решение 25): фронтенд в этом случае просто запрашивает следующую страницу;
  - ответ `{items, next_cursor, has_more, end_reason}`. Элемент: `{profile: <публичная карточка>, match_type, match_score, is_viewed, reasons}`.
- `GET /api/v1/feed/cards/{profile_id}` — тот же элемент, `match_type: null`.
- `POST /api/v1/feed/views {profile_id}` → 204. Невидимая или своя анкета → 404.
- Коды: 409 `username_required`, 409 `no_active_profile`, 400 `invalid_filter`, 400 `invalid_cursor`, 422 (нарушены границы схемы или `mode=search` без фильтров).
- Публичная схема карточки — отдельный `FeedProfileSchema`, а не `ProfileSchema`: в ней нет `status`, `is_active` и `updated_at`, зато есть `first_name` и `photo_url`.

### Config
- `core/config.py`, `FeedConfig` (plain `BaseModel`, `Settings.feed: FeedConfig = FeedConfig()`):
  - веса: `weight_semantic 0.4`, `weight_tags 0.4`, `weight_role 0.2`;
  - роль: `role_affinity_same_role 1.0`, `role_affinity_same_category 0.5`;
  - активность: `activity_recent_days 7`, `activity_stale_days 30`, `activity_factor_recent 1.0`, `activity_factor_stale 0.8`, `activity_factor_old 0.5`;
  - сессия и страницы: `session_ttl_seconds 1800`, `session_cap 200`, `page_size_default 20`, `page_size_max 50`;
  - семантика: `bio_tier_min_cos_sim 0.35`, `similar_bio_reason_min_cos_sim 0.5`;
  - причины: `max_reason_tags 5`;
  - валидатор: сумма весов равна 1 через `math.isclose` (во float `0.1 + 0.2 + 0.7 != 1.0`), пороги активности упорядочены, `0 ≤ min_cos ≤ 1`.
- `container.py`: `current_embedding_model()` (решение 21), `feed_repo()`, `profile_view_repo()`, `feed_session_repo()`, `feed_scorer()`, `feed_ranker()`, `feed_filter_validator()` и три use case.

## 6. Сид `scripts/dev_seed_profiles.py`

**Запуск:** `python -m scripts.dev_seed_profiles [--reset]`. Нужны `alembic upgrade head` и `dev_seed_taxonomy`. При `run.env == prod` скрипт сразу завершается с ошибкой, ничего не делая (решение 32).

**Фикстура** `scripts/data/seed_profiles.json`, массив пользователей:

```json
{
  "key": 1,
  "first_name": "Анна", "last_name": "К.", "username": "seed_anna_backend",
  "last_active_days_ago": 2, "is_banned": false,
  "profiles": [{
    "category": "tech_product", "role": "engineering",
    "country_code": "RU", "timezone": "Europe/Moscow",
    "extra_attributes": {"specialization": "backend", "grade": "senior", "workload": "part_time", "format": "remote"},
    "tags": ["Python", "FastAPI", "PostgreSQL", "Docker", "Redis"],
    "bio": "…200–2000 символов…", "goals": "…",
    "status": "active", "skip_embedding": false
  }]
}
```

Все `username` в фикстуре начинаются с `seed_` (решение 32), скрипт это проверяет.

**Состав** (около 90 пользователей и 100 профилей):
- Tech & Product ≈ 60: founder 10, product_project 8, engineering 18, design 8, marketing 6, sales_bizdev 5, finance_legal 5.
- Edu & Growth ≈ 40: study_mate 10, language_buddy 12, pet_project_partner 10, mentor_mentee 8.
- Граничные случаи, специально:
  - около 10 пользователей с двумя профилями (часть в одной категории, часть в разных);
  - 5 профилей на паузе;
  - 3 пользователя без username;
  - 2 забаненных;
  - 2–3 профиля с `skip_embedding`;
  - `last_active_days_ago`: примерно 60% ≤ 7, 25% ≤ 30, 15% старше;
  - внутри роли разные специализации и грейды, чтобы каскад Search давал непустые tier 1 и 2;
  - разные таймзоны с одинаковым смещением (например, `Europe/Moscow`, `Europe/Kirov`, `Europe/Istanbul`), чтобы проверить `same_timezone`;
  - 2–3 профиля с pending-тегом, чтобы проверить решение 28.

**Алгоритм:**
1. `telegram_id = 10**12 + key`. Этот диапазон не пересекается с реальными id Telegram, `--reset` удаляет пользователей из него (каскад уберёт профили, теги, эмбеддинги и просмотры).
2. Пользователи: `INSERT ... ON CONFLICT (telegram_id) DO NOTHING`. Профиль уже есть по `(user, category, role)` — пропускаем. Повторный запуск ничего не дублирует и не перезаписывает тексты.
3. Теги по `normalize_tag_title`: нет тега — создать `approved` (`created_by_user_id = NULL`); нет scope `(category, role)` — добавить (решение 15). Тег, помеченный в фикстуре как pending, создаётся `pending` с автором — сид-пользователем.
4. Каждая анкета проходит `ProfileContentValidator.validate`. Невалидная — скрипт останавливается и печатает ключ и причину, в БД ничего не пишется (одна транзакция).
5. В той же транзакции `mark_pending` для каждого нового профиля, `active_profile_id` — первый активный профиль пользователя, `last_active_at = now - N дней`. Затем коммит.
6. Для новых профилей без `skip_embedding` вызвать `EmbedProfileUseCase.execute(id, is_last_attempt=True)` с провайдером из `.env`. Каждый вызов в своей сессии, по образцу `_run_with_container` из `app/worker/tasks.py`. Итог печатается: ready, failed, skipped.
7. `usage_count` тегов увеличивается так же, как в `CreateProfileUseCase` (`increment_tags_usage`).

**Переход на OpenAI** не требует пересида: смени провайдер в `.env`, и sweep пересчитает анкеты с другой `model`. Чтобы не ждать sweep, есть флаг `--reembed`: он синхронно вызывает embed для всех сид-профилей.

**`scripts/dev_gen_init_data.py`:** добавить `--username`, `--first-name`, `--no-username` (раздел 3, пункт 5).

## 7. Тесты

Правила `docs/rules.md`, раздел 4, действуют полностью.

**Unit (`tests/unit`, без БД и Redis)**
- `test_feed_scorer`:
  - веса на ручных примерах;
  - `tag_overlap` (пустые множества, разный размер);
  - границы активности ровно 7 и 30 дней (`freezegun`);
  - деградация по паре: нет вектора у смотрящего, нет у кандидата;
  - отрицательный cos обрезается;
  - причины детерминированы и ограничены `max_reason_tags`;
  - `role_affinity = 0` для чужой категории.
- `test_feed_ranker`:
  - каскад: tier 1 → 2 → 3 по порядку;
  - кандидат попадает только в высший tier;
  - внутри tier 2 «2 из 3» выше «1 из 3» даже при меньшем score; при равном числе совпадений — по score;
  - фильтр из одного критерия: tier 2 пуст;
  - причина `matched_filters` с правильными `matched` и `missed`;
  - bio-запрос не нужен, если tier 1+2 набрали кап;
  - кап 200;
  - тай-брейкер по `profile_id`;
  - Discovery — один уровень.
- `test_feed_session_fingerprint`: перестановка тегов и атрибутов не меняет ни `params_hash`, ни ключ сессии; смена режима или фильтров меняет `params_hash`; правка профиля, появление вектора и смена версии алгоритма меняют ключ сессии, но не `params_hash`.
- `test_feed_use_cases` с фейками:
  - `no_active_profile` (нет указателя; профиль не `active`);
  - попадание в сессию не вызывает `list_*_candidates`;
  - промах строит и сохраняет сессию;
  - Redis отдаёт `None` — лента работает;
  - курсор со старым ключом сессии, которая ещё жива, режется из неё, хотя текущий ключ другой (появился вектор);
  - курсор с истёкшей сессией — пересчёт и срез с того же смещения;
  - курсор с ключом чужой сессии (другой `viewer_profile_id`) — чужие элементы не отдаются, строится своя сессия;
  - другой `params_hash` в курсоре → `InvalidFeedCursorError`;
  - `NO_CANDIDATES_YET` против `END_OF_FEED`;
  - выпавший при гидрации кандидат;
  - просмотр: своя или невидимая анкета → ошибка, коммит один.
- `test_feed_filter_validator`: роль из чужой категории, атрибут без роли, нефильтруемое поле, значение не из `options`, неизвестная страна, pending, rejected и несуществующий тег.
- `test_timezone_catalog`: зоны с тем же смещением (Москва, Киров, Стамбул вместе; летнее время учитывается через `freezegun`).
- `test_feed_cursor`: битый base64, не JSON, список вместо объекта, `bool` вместо `int`, отрицательное смещение, огромное смещение, не-ASCII, хэш не той длины.
- `test_settings`: валидатор `FeedConfig`, включая веса с суммой 1 через `isclose`.
- Фейки в `tests/fixtures/`: `FakeFeedRepository`, `FakeFeedSessionRepository`, `FakeProfileViewRepository`. Они реализуют настоящие ABC, возвращают копии и применяют то же правило видимости.

**Integration (`tests/integration`)**
- `test_feed_repository.py`, на реальном Postgres со сконструированными векторами (единичные векторы с заданным косинусом):
  - видимость: себя, свою вторую анкету, paused, hidden, забаненного, без username, с пустым username исключаем;
  - cos считается только при совпадении `model`, stub против OpenAI даёт NULL;
  - совпавшие критерии по каждому типу (роль, атрибут, незаполненный атрибут = не совпал, теги, страна, таймзона по списку зон); кандидат с 0 совпадений не возвращается;
  - rejected-теги не попадают ни в `tag_ids`, ни в карточку, pending попадают;
  - bio-tier: чужая категория, порог, `exclude`;
  - `get_cards` сохраняет порядок и правильный `is_viewed` по профилю смотрящего.
- `test_feed_endpoints.py`, по HTTP:
  - каскад проваливается в tier 2 и 3 при узких фильтрах;
  - при пустых tier показывается пустой экран, а не случайные анкеты;
  - порядок стабилен при повторных запросах **и после `FLUSHDB`** (пересчёт даёт тот же порядок);
  - страницы не дублируют друг друга;
  - смена фильтров со старым курсором → 400;
  - смена ключа сессии посреди листания (правка своей анкеты, появление своего вектора) — следующая страница по старому курсору 200, без повторов;
  - `same_timezone`: в выдаче только кандидаты с тем же смещением в tier 1;
  - pending-тег в фильтре → 400 `invalid_filter`;
  - 409 `username_required` на всех трёх эндпоинтах, 409 `no_active_profile`;
  - лента работает при недоступном Redis;
  - враждебный ввод на всех параметрах → 4xx;
  - бан смотрящего → 403 (через `get_current_user`).
- `test_profile_view_repository.py`: идемпотентность; **параллельные** вставки через `asyncio.gather` дают одну строку без ошибки (правило «Concurrency»); каскадное удаление с обеих сторон.
- `test_redis_feed_session.py`: round-trip, битое значение, недоступный Redis (по образцу `test_redis_taxonomy_cache.py`).
- `test_seed_profiles.py`, со смыслом, а не подсчётом строк:
  - каждая анкета проходит валидатор;
  - граничные случаи присутствуют;
  - повторный запуск ничего не меняет;
  - с stub все профили без `skip_embedding` получают `ready`;
  - `--reset` чистит только сид-диапазон;
  - при `env=prod` скрипт отказывается работать и ничего не пишет (в том числе с `--reset`);
  - все username начинаются с `seed_`.
- `conftest.py`: добавить `profile_views` в `TRUNCATE`.

## 8. Как проверить (результаты прикладывать)

```
docker compose up -d --build
docker compose exec found_core_mini_app alembic upgrade head
docker compose exec found_core_mini_app alembic check      # ожидается: No new upgrade operations detected
pytest -v --cov=app --cov-report=term-missing              # с хоста, из .venv: в образе нет pytest
docker compose exec found_core_mini_app python -m scripts.dev_seed_taxonomy
docker compose exec found_core_mini_app python -m scripts.dev_seed_profiles
```

Ручной сценарий:
1. Залогиниться под сид-пользователем: `python scripts/dev_gen_init_data.py --telegram-id 1000000000001 --username seed_anna_backend --first-name Анна`, затем `POST /auth/telegram`.
2. `GET /feed?mode=discovery`: 20 карточек Tech & Product, нет своей, нет paused, нет забаненных, нет без username, `match_score` убывает.
3. Второй вызов с `next_cursor`: следующие 20 без повторов. `redis-cli -n 0 KEYS 'feed:session:*'`: одна сессия.
4. `POST /feed/views` для карточки, затем повтор первой страницы: та же позиция, `is_viewed: true`.
5. `GET /feed?mode=search&role_id=<engineering>&attr=grade:senior&tag_ids=<Go>`: сначала `EXACT_MATCH`, потом `PARTIAL_MATCH`, внутри него сначала «2 из 3», у каждого причина `matched_filters`. Затем добавить `&same_timezone=true`: в tier 1 только зоны с тем же смещением.
6. `POST /profiles/{другой}/activate` и снова лента: другая категория, новая сессия.
7. `docker compose stop redis`: лента отвечает, просто медленнее.
8. С ключом OpenAI: `--reembed`, затем сравнить выдачу и записать наблюдения о пороге tier 3 в раздел 12.

## 9. Риски и что калибровать

- **Пороги семантики** (`bio_tier_min_cos_sim 0.35`, `similar_bio_reason_min_cos_sim 0.5`) — догадка. У `text-embedding-3-small` на однотипных русских анкетах косинус обычно 0.3–0.6. Калибровать на сиде с OpenAI.
- **Абсолютный `match_score`** даже у идеальной пары вряд ли превысит ~80: реальный cos далёк от 1. Шкалу не растягиваем без данных (`CLAUDE.md`, «Match-percentage calibration»).
- **Производительность:** кандидаты категории читаются целиком без LIMIT, tier 3 делает seq scan по векторам. Нормально до нескольких тысяч анкет (решения 6 и 20).
- **Кап 200** (`CLAUDE.md`, «Risks»): хвост категории в одной сессии не виден.
- **Нет rate limiting (решение 30, принятый риск):** сборки сессий не ограничены, а `GET /feed/cards/{id}` позволяет выкачать все видимые анкеты перебором id. Закрыть вместе с общим rate limiting.
- **Pending-теги видны всем в карточках** (решение 28): немодерированный текст до этапа 8. Фронтенд рендерит теги, `bio` и `goals` только как текст.
- **Упрощения формулы, которые стоит проверить на сиде** (в этап 6 не входят): все теги весят одинаково (популярный `Python` = редкий `FastAPI`; позже можно учесть редкость через `usage_count`); атрибуты (`grade`, `format`, `workload`) в score не участвуют (TODO `is_embedded` из `CLAUDE.md`); у всех одни и те же люди наверху, нет разнообразия; просмотренные карточки остаются на месте и при каждом новом заходе стоят сверху; критерии tier 2 равноценны.

## 10. Порядок работы (три части)

**6a: сид и домен формулы**
1. `FeedConfig`, enums, сущности, `FeedScorer`, `FeedRanker`, `params_hash` и ключ сессии, метод «зоны с тем же смещением» в `TimezoneCatalog`, unit-тесты.
2. Флаги в `dev_gen_init_data.py`, фикстура, `dev_seed_profiles.py`, `test_seed_profiles.py`.
3. Итог: сид работает, формула покрыта тестами, API ещё нет.

**6b: SQL кандидатов**
1. `IFeedRepository` (кандидаты, `get_candidate`), `SqlAlchemyFeedRepository`, `FeedFilterValidator`, integration-тесты репозитория.
2. Итог: на сиде через тест или скрипт можно получить ранжированный список для любого профиля.

**6c: сессии, просмотры, API**
1. `profile_views` (модель, миграция, репозиторий), `get_cards` с `is_viewed`.
2. `RedisFeedSessionRepository`, три use case, контейнер.
3. `require_username`, схемы, мапперы, курсор, роутер, integration-тесты эндпоинтов.
4. Обновить `CLAUDE.md` (раздел 11).

## 11. Документация

Обновить `CLAUDE.md`:
- таблица этапов и «Repository state»;
- «Feed algorithm» (planned → as built: решения 3–7, 10, 18, 20, 28, 31, 33; новое определение tier 2 заменяет «category matches, or at least one shared tag»);
- «Data model» (`profile_views`);
- «API» (эндпоинты ленты, коды ошибок, формат курсора и причин);
- карта Redis (сессии ленты в DB кэша);
- «Layered architecture» (новые модули);
- «Follow-up PRs» (`require_username` готов, осталось повесить на категории и `POST /profiles`);
- «Risks» (решение 30: лента без rate limiting до общего rate limiting);
- снять пункт про сид на ~100 профилей из открытых.

## 12. Отклонения от плана

**Ревью плана до начала работы** (security и архитектура; все пункты утверждены пользователем):

1. **Курсор и сессия** (решения 10, 18). Было: один отпечаток в курсоре, любое его изменение → 400. Из-за этого появление своего вектора посреди листания или деплой с новой версией алгоритма ломали следующую страницу. Стало: `params_hash` (должен совпасть) и ключ сессии (может устареть); истёкшая сессия пересчитывается с того же смещения; версия алгоритма входит в ключ сессии; сессия хранит владельца, и use case проверяет его, потому что ключ приходит от клиента.
2. **Фильтр `same_timezone`** (решения 2, 3). Было: фильтра по таймзоне нет. Стало: галочка «в моём часовом поясе» по текущему UTC-смещению, зоны считаются в Python через `TimezoneCatalog`.
3. **Tier 2** (решения 4, 33). Было: только по тегам фильтра, а без тегов — вся категория; инженер-сеньор без нужного тега выпадал из Search. Стало: хотя бы один совпавший критерий, сортировка по числу совпавших, потом score; причина `matched_filters`.
4. **Статусы тегов** (решение 28). Фильтр — только approved, иначе 400; карточки и формула — все теги кроме rejected.
5. **Username-gate на всех трёх эндпоинтах ленты** (решение 13). Было явно указано только для `GET /feed`.
6. **Защита сида** (решение 32): запрет в prod, префикс `seed_`.
7. **Пропуск bio-запроса** при заполненном капе (решение 31).
8. **`math.isclose`** в валидаторе суммы весов `FeedConfig`.
9. **Принятые риски** (решение 30): нет лимита на сборку сессий и на `GET /feed/cards/{id}` до общего rate limiting.
10. **Близость ролей оставлена как есть** (решение 29) после обсуждения: Discovery — это похожие люди.

Отклонено на ревью: проверка, не содержит ли `photo_url` username (не критично); вынос имени модели `stub-v1` в константу провайдера (не критично, заглушка).

**Отклонения по ходу работы:**

11. **Отпечаток параметров скоринга в ключе сессии** (решение 18). Было: ключ сессии = `params_hash` + `updated_at` + флаг вектора + версия алгоритма; смена весов в конфиге вступала в силу только через TTL сессии, и разные пользователи могли получать выдачу по разным формулам. Стало: в ключ добавлен `FeedConfig.scoring_fingerprint()` (веса, коэффициенты роли и активности, пороги косинуса, `max_reason_tags`). `FEED_ALGORITHM_VERSION` остаётся для изменений кода, отпечаток покрывает изменения чисел. Не входят `session_ttl_seconds`, размеры страниц и `session_cap`. `build_feed_session_key` принимает отпечаток аргументом, use case получает его строкой через конструктор.
12. **Причина `shared_tags` хранит только `tag_ids`** (решение 12). Было: `{"id", "title"}` в сессии. Стало: в сессии только id, названия подставляются при гидрации страницы из тегов карточки (общие теги всегда подмножество тегов кандидата). Формат в ответе API не меняется. Так сессия компактнее, а переименованный тег не остаётся в кэше старым.

13. **Фикстура сида разбита на четыре файла** (раздел 6): `scripts/data/seed_profiles/{tech_engineering,tech_product_design,tech_business,edu_growth}.json` вместо одного `seed_profiles.json`. Скрипт читает все `*.json` каталога по алфавиту; формат записи тот же, ключи пользователей уникальны во всех файлах вместе. Причина: сто анкет с текстами на русском не помещаются в одну запись, а четыре файла по категориям удобнее ревьюить.
14. **Анкеты с `skip_embedding` не получают строку в `profile_embeddings` вообще** (раздел 6, пункт 5). Было: `mark_pending` для каждой новой анкеты. Но тогда sweep (он работает по этой таблице) досчитал бы вектор через пять минут, и «анкета без вектора» перестала бы быть постоянным случаем для проверки деградации. Теперь `mark_pending` и embed только для анкет без `skip_embedding`.
15. **Скорер сам обрезает косинус в `[0, 1]`** (решение 22). Нижняя граница по-прежнему задаётся в SQL (`GREATEST(0, ...)`), верхняя и нижняя в `FeedScorer` — страховка от шума float вида `1.0000001` и от ошибки в запросе.
16. **Флаг сида `--reembed` берёт только анкеты, у которых есть строка в `profile_embeddings`**, чтобы не досчитать вектор тем, кому его намеренно не делали (см. пункт 14).

17. **`IFeedRepository.get_cards` принимает `FeedViewer`, а не `viewer_profile_id`** (раздел 5). Условию видимости нужен `user_id` смотрящего (чужие анкеты и собственные вторые анкеты исключаются по пользователю), а `is_viewed` считается по `viewer.profile_id`.
18. **`mode=discovery` с фильтрами в запросе даёт 422**, как и `mode=search` без фильтров (решение 27, раздел 5). Иначе фильтры молча игнорировались бы, а клиент считал бы, что они применены.
19. **`FeedConfig.max_cursor_offset`** (по умолчанию 10 000, не меньше `session_cap`): граница смещения в курсоре («огромное смещение» из раздела 5 должно быть настраиваемым числом, а не константой в маппере). В отпечаток скоринга не входит.
20. **`FeedViewerResolver`** (`application/services/feed_viewer_resolver.py`): общая проверка «активный профиль есть и он `active`, считается ли у него вектор текущей модели» для трёх use case ленты вместо трёх копий одного кода.
21. **Причина отказа фильтра `reason`** в теле 400 (`attributes_require_role`, `role_not_in_category`, `attribute_not_filterable`, `attribute_value_invalid`, `attribute_malformed`, `country_unknown`, `tag_invalid`, `filters_required`). Неизвестный id тега и тег не в статусе `approved` дают один и тот же `tag_invalid`, чтобы по ответу нельзя было узнать, существует ли чужой pending-тег.
22. **Тесты запускаются с хоста из виртуального окружения**, а не через `docker compose exec`: образ ставит только `requirements.txt` (команда `pytest` в разделе 8 исправлена, правило записано в `CLAUDE.md`, «Tech stack»).

23. **`SqlAlchemyFeedRepository` переписан с `text()` на выражения SQLAlchemy** (раздел 5, «Infrastructure»; набросок SQL там остаётся описанием запроса, а не кода). Причина: единообразие с остальными репозиториями и привязка имён таблиц и колонок к моделям. Правило «no register_vector» этому не мешает: оно запрещает кодек asyncpg для векторов, пересекающих драйвер, а здесь `<=>` сравнивает две колонки внутри Postgres. Что изменилось в поведении по построению: Search без критериев возвращает `[]` без запроса (раньше запрос возвращал пусто); условие «совпал хотя бы один критерий» стало `OR` условий вместо `cardinality(matched_filters) > 0`; `= ANY(:list)` и `<> ALL(:list)` стали `IN` и `NOT IN` (списки не больше `session_cap`); `get_cards` делает два запроса вместо трёх (видимость проверяется в основном запросе). Идентичность поведения проверена временным тестом, который гонял старую и новую реализацию на одних данных (смотрящие с вектором, без вектора и с вектором другой модели; все комбинации критериев; bio при порогах от 0; пустые списки; карточки); после зелёного прогона тест и копия старой реализации удалены.

Дальнейшие отклонения дописываются сюда по ходу работы.

## 13. Не входит в этап 6

Открытие контакта, лимиты и уведомление бота (этап 7). Модерация и админский «почему рекомендовано» (этап 8). Аналитика (этап 9). Rate limiting ленты (решение 30). Свободный текстовый поиск. Фильтр «±N часов» (есть только `same_timezone`). Настраиваемая близость ролей (решение 29). Веса тегов по редкости, атрибуты в score, разнообразие выдачи (раздел 9). `is_embedded` для `role_fields`. Follow-up PR про `terms_accepted_at` и `tags/suggest`. Перенос формулы в SQL и использование HNSW (решения 6 и 20, «когда пересмотреть»).
