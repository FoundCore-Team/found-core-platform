# CLAUDE.md (frontend)

This file provides guidance to Claude Code (claude.ai/code) when working with code under `frontend/`. The product, the domain model and the backend are described in the root [`CLAUDE.md`](../CLAUDE.md); read it first. **Mandatory frontend rules (do/don't) are in [`docs/rules.md`](docs/rules.md); read them before writing or changing any frontend code.** This file is the source of truth for the frontend architecture and context; `docs/rules.md` is the normative rulebook.

## Project overview

The frontend is the Telegram Mini App of FoundCore: onboarding, the profile form (anketa), the user's profiles, the feed with filters and search, and opening a contact. It talks to the backend in this repository through `/api/v1` and nothing else. The mechanics (no swipes, a feed with filters, a contact that opens immediately, a notification to the profile owner) are defined in the root `CLAUDE.md` and are not repeated here.

Taxonomy-driven parts of the UI (categories, roles, role fields, tags, countries, time zones, text limits) are never hardcoded: they come from `/taxonomy/*` and `/profiles/form-config`.

Out of scope for now: the admin panel and reports (backend stage 8), analytics events (stage 9), the history of opened contacts (a backend follow-up).

## Repository state

Nothing is implemented yet. This branch only adds the documents: this file, [`docs/rules.md`](docs/rules.md) and the plans in [`docs/plans/`](docs/plans/). The work is split into stages F0–F9 (see "Development stages"); each stage is its own `feat/frontend-...` branch and its own PR. The next stage is F0 (`skeleton`), planned in [`docs/plans/f0-skeleton.md`](docs/plans/f0-skeleton.md); the plan of all stages with every decision is [`docs/plans/overview.md`](docs/plans/overview.md).

## Stack

Versions were checked against the npm registry on 2026-11-06. `package-lock.json` is the final word; the limits below are what must stay true.

| Area | Choice | Constraint and reason |
|---|---|---|
| Core | React 19, Vite 8, TypeScript (strict) | A Mini App is a client-side app in a WebView; no server rendering |
| Telegram | `@tma.js/sdk-react` (one package) | `@tma.js/sdk` is its dependency; do not install it a second time. The old `@telegram-apps/*` line is not used |
| Server state | TanStack Query 5 | cache, retries, `useInfiniteQuery` for the feed cursor |
| Routing | React Router 8, browser history | needs Node 22.22+ and React 19.2.7+; no hash router (Telegram puts launch params in the fragment) |
| Styles | CSS Modules and tokens in CSS variables | no UI library |
| API types | `openapi-typescript` 7 and `openapi-fetch` from `/openapi.json` | `detail` error shapes are not in the schema and are typed by hand |
| TypeScript version | **5.9.x, not newer** | 7.x breaks `typescript-eslint` (needs `<6.1`) and `openapi-typescript` (needs `^5`) |
| Lint | ESLint **9.x**, `typescript-eslint` strict-type-checked, `jsx-a11y`, `react-hooks`, Prettier | `eslint-plugin-jsx-a11y` does not support ESLint 10 |
| Tests | Vitest 5, Testing Library, MSW 3, jsdom 31 | Vitest 5 needs Node 22.12+; jsdom 31 needs Node 22.22.2+ or 24.15+ |
| Vite plugin | `@vitejs/plugin-react` 6 | the line that supports Vite 8 |
| Package manager | npm | ships with Node. `engines` is `^22.22.2 \|\| ^24.15.0 \|\| >=26.0.0` (the strictest requirement is jsdom); use a recent Node 24.x, 24.14.1 is too old |

Not used on purpose: Telegram UI (many open issues, unclear maintenance, and the brand needs its own look), Next.js (no server rendering needed), a global state store and a form library (decided in stage F3 if needed).

## Directory structure

```
frontend/
  CLAUDE.md
  docs/rules.md
  docs/plans/            overview.md, f0-skeleton.md, then one plan per stage
  src/
    app/                 startup, providers, router, gates, global styles and tokens
    shared/
      api/               client, generated schema, error normalization
      telegram/          the only place that touches the Telegram SDK
      ui/                component kit
      i18n/              the Russian dictionary
      config/            env validation, frontend-only tunables
    features/            auth, onboarding, profile-form, my-profiles, feed, search, contact
  tests/                 shared fixtures and MSW handlers
```

Dependencies point down only (`app` → `features` → `shared`). See rules.md, section 1.

## Entry gates

After sign-in the app decides where to send the user, in this order; the first match wins.

1. Not inside Telegram (production only): a short "open it from Telegram" screen.
2. Loading or sign-in failed: loading or error screen with a retry.
3. `user.is_banned`: the ban screen with `ban_reason`.
4. `user.username` is empty: the screen that explains how to set a Telegram username, with a "check again" button (a fresh sign-in).
5. Terms not accepted: the terms screen (needs a backend endpoint, see "Backend dependencies").
6. `profiles` is empty: onboarding into the profile form.
7. All profiles are `paused`: the "resume a profile" screen.
8. Otherwise the feed.

A deep link `?profile_id=<id>` (from the bot notification) opens that card after the gates pass.

## API contract used by the frontend

All paths are under `/api/v1`. The backend is the source of truth; if this section disagrees with `/openapi.json` or the root `CLAUDE.md`, they win.

* Auth: `POST /auth/telegram {init_data}` → `access_token`, `is_new_user`, `user`, `profiles`. A banned user still gets `200`; `user.is_banned` and `ban_reason` are in the response. The token lives 24 hours and there is no refresh: on `401` sign in again with fresh `initData` (Telegram rejects `initData` older than 300 seconds).
* Taxonomy (token): categories, roles of a category, fields of a role, `tags/suggest`, `POST tags/custom` (the answer carries the canonical title; replace what the user typed).
* Profiles (token): `GET /profiles/form-config`, `GET /profiles/me`, `POST /profiles`, `GET|PUT|DELETE /profiles/{id}`, `POST /profiles/{id}/activate|pause|resume`. At most one profile per (category, role); category and role cannot be changed after creation; `PUT` replaces the whole profile.
* Feed (token and username): `GET /feed?mode=discovery|search&role_id&attr=key:value&tag_ids&country_code&same_timezone&cursor&limit`, `GET /feed/cards/{profile_id}`, `POST /feed/views {profile_id}` (`204`, sent only when the full card is opened). `mode=search` without a filter and `mode=discovery` with filters are both `422`.
* Contact (token and username): `POST /profiles/{profile_id}/contact` → `{username, is_new_open, remaining_today}`; `201` for a first open, `200` for a free repeat.

Error `detail` has four shapes: a string (most `404`, `401`, `403`; for a ban it is the reason), an object `{code, message, ...}`, an object `{message, profile}` (a duplicate profile), and a list (`422`). Codes: `no_active_profile` (409), `username_required` (409), `contact_limit_reached` (429, with `limit` and `resets_at`), `invalid_filter` (400, with `reason`), `invalid_cursor` (400).

Enumerations: `match_type` is `DISCOVERY`, `EXACT_MATCH`, `PARTIAL_MATCH` or `RECOMMENDED_BY_BIO`; `end_reason` is `NO_CANDIDATES_YET` or `END_OF_FEED`; reason codes are `matched_filters` (with `matched` and `missed`), `shared_tags` (with `tags`), `same_role`, `same_country`, `same_timezone`, `similar_bio`, `recently_active`; profile status is `active`, `paused` or `hidden_by_admin`; tag status is `approved`, `pending` or `rejected`.

Behaviors the UI must respect:

* Cards carry no `username` and no `last_name`; the username comes only from the contact endpoint. A card has `is_viewed` (render grey, with a non-color cue) and `is_contact_opened` (show "Write" instead of "Open contact").
* Reasons arrive as codes with parameters; the frontend writes the text. Country names are English; localize by code. A time zone label is the city part of the IANA id.
* `match_score` is 0–100. In the `RECOMMENDED_BY_BIO` tier it is low by construction (about 7–28), so the percentage is shown modestly there until the backend decision (see "Risks").
* The feed is paged by an opaque `cursor`; the server keeps a ranking session for 30 minutes (200 candidates at most) and a new filter set starts a new one. Do not parse the cursor.
* Tags, `bio` and goals of other users are plain text; pending tags appear on cards before moderation.

## Theme and platform

The app follows the Telegram theme, light or dark, through `--tg-theme-*` variables; the brand accent `#10b981` is the only fixed color. The source of the theme is Telegram itself, not the device setting. Full platform rules (safe areas, BackButton, version gating, closing confirmation, performance) are in [`docs/rules.md`](docs/rules.md), section 5.

## Local development

* **Without a backend and outside Telegram.** `VITE_ENABLE_MOCKS=true` starts MSW and the Telegram mock environment. Works only in development mode.
* **Against the local backend.** Run the backend as described in the root `CLAUDE.md`, seed it (`python -m scripts.dev_seed_taxonomy`, `python -m scripts.dev_seed_profiles`) and put the output of `scripts/dev_gen_init_data.py` (it uses only the Python standard library) into `VITE_DEV_INIT_DATA`. The data expires after 300 seconds; generate it again. Seed users have usernames starting with `seed_`.
* **Inside real Telegram.** A phone does not accept self-signed certificates: use an https tunnel and a test bot from BotFather, and put that bot's token into the backend `.env` (`APP_CONFIG__BOT__TOKEN`). Check iOS, Android and one desktop client before a release.
* The exact commands (`npm ci`, `npm run dev`, `npm test`, ...) are listed in each stage plan; the user runs them (rules.md, section 3).
* **Development machine (Fedora Linux).** Before the first commit: an SSH key registered on GitHub both as an authentication key and as a signing key, and `git` configured to sign every commit (rules.md, section 3). Node 24.15+ (install it through `fnm` or `nvm`, not the system package), Python 3 for `scripts/dev_gen_init_data.py`. The local backend needs a container runtime, Docker or Podman; Fedora runs SELinux in enforcing mode, and the bind mounts `.:/code` in the backend `docker-compose.yaml` may need the `:z` suffix. Stages F0 and F1 run on mocks and do not need the backend, so the runtime can be set up later.

## Development stages

Each stage is its own `feat/frontend-...` branch and its own PR. Details in [`docs/plans/overview.md`](docs/plans/overview.md).

| # | Stage | Content |
|---|-------|---------|
| F0 | skeleton | **Next.** Vite/React/TS, lint, tokens, Telegram wrapper, typed API client, error normalization, sign-in and session, ban screen, app shell, mocks, frontend CI job. Plan: `docs/plans/f0-skeleton.md` |
| F1 | ui-kit | Screen map and states, Russian copy, component kit, a development-only showcase |
| F2 | gates | Entry gates: username, terms, no profiles, all paused |
| F3 | profile-form | The anketa: category, role, role fields, common fields, tags, review |
| F4 | my-profiles | List, switch the active one, pause, resume, edit, delete |
| F5 | feed | Cards, paging, grey viewed, reasons, full card, empty states |
| F6 | search | Filters sheet, three result tiers, filter chips |
| F7 | contacts | Open contact, "Write", daily limit, deep link from the notification |
| F8 | polish | Accessibility, safe areas, haptics, animation, offline, bundle size |
| F9 | release | Static build served with the backend (backend stage 10), BotFather, real-device pass |

Stages F0 and F1 do not need new backend work; F2 and F9 do (see below).

## Backend dependencies

Requested from the backend owner; none blocks F0 or F1.

| # | Request | Needed by |
|---|---|---|
| 1 | an endpoint to accept the terms and `terms_accepted_at` in the `/auth/telegram` response | F2 (until then the terms screen is a stub marked in code) |
| 2 | CORS for the Mini App origin, or one origin for the frontend and the API behind nginx | F9 (development uses the Vite proxy) |
| 3 | `require_username` on the taxonomy endpoints and `POST /profiles` | F2 (until then the frontend checks `user.username` itself) |
| 4 | `/openapi.json` stays available at least in development | F0 |
| 5 | a real bot and an https Mini App URL | F9 |

## Risks and open questions

* **Quality of the match percentage.** The backend records that the score in the `RECOMMENDED_BY_BIO` tier is low by construction and that the similarity thresholds barely filter. Until that is decided, the UI shows the percentage modestly and never as the main signal.
* **Contract drift.** Generated types catch schema changes, not the `detail` shapes; those are covered by tests and must be re-checked after each backend stage.
* **Telegram quirks.** The platform behaves differently on iOS, Android and desktop, and old clients lack newer methods; only a pass on real devices finds these (stage F9, and earlier during F2–F7 whenever a stage touches the platform).
* **Unmoderated tags on public cards** until backend stage 8: plain text only.
* **No rate limiting on the backend yet:** the UI must not poll or fire requests in loops (debounce suggestions, one request per page).
* **Fonts.** A system font stack gives the native feel and costs nothing to load; whether the brand font is worth loading is decided in F1.
