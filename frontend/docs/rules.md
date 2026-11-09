# FoundCore Frontend Development Rules

These are mandatory development and code style rules for everything under `frontend/`. The backend rules stay in [`../../docs/rules.md`](../../docs/rules.md); where a rule there is about process (git, validation, tests), it applies here too. [`../CLAUDE.md`](../CLAUDE.md) is the source of truth for context and architecture.

## 1. Architecture

### Layout and dependency direction

* Source lives in `frontend/src/` and has three top-level folders: `app/` (startup, providers, router, gates), `features/` (one folder per user-facing capability) and `shared/` (api, telegram, ui, i18n, config).
* Dependencies point down only: `app` may import `features` and `shared`; `features` may import `shared`; `shared` imports neither. A feature never imports another feature's internals; it uses that feature's `index.ts`.
* Name a module after what it does, not after the nearest data type.

### Network

* Only `shared/api` talks to the network. `fetch` and `XMLHttpRequest` are banned elsewhere by lint.
* Components never build URLs or read `Response` objects; they call typed hooks.
* `shared/api/schema.d.ts` is generated from the backend `/openapi.json` (`npm run api:types`). Never edit it by hand. Regenerate it in the same PR as the feature that needs the new schema.
* The `detail` shapes of errors are not in OpenAPI. They are typed by hand in `shared/api/errors.ts` and covered by tests (see "Errors").

### Telegram

* Only `shared/telegram` imports `@tma.js/sdk-react` or touches `window.Telegram`. Everything else uses its typed wrapper, which is version-gated and does nothing outside Telegram.
* Install `@tma.js/sdk-react` only. `@tma.js/sdk` comes with it; a second copy breaks the shared state.

### Server state and client state

* Server data goes through TanStack Query, always. Never fetch in `useEffect`.
* One function per feature builds that feature's query keys.
* Everything else is local `useState` or `useReducer`. Context is for cross-cutting concerns only (session, Telegram). No global store until two features really share non-server state.

### Errors

* Every request failure becomes a normalized `ApiError` (`kind`, `status`, `code?`, `message`) in `shared/api`. The backend `detail` is a string, an object `{code, message, ...}`, an object `{message, profile}` or a list (422). A network failure or a non-JSON body is also an `ApiError`.
* Screens switch on `code` or `status`, never on message text. An unknown shape is a generic error, never a crash.
* A `403` with a string `detail` on any endpoint means the user is banned; the detail is the ban reason.

### Session

* The access token lives in memory only. Never in `localStorage`, `sessionStorage`, cookies or IndexedDB: Telegram clients may wipe them, and a token there outlives the session.
* On `401`, re-authenticate once with fresh raw `initData`, then retry the original request once. Re-authentication is single-flight: parallel requests share one sign-in. A second failure goes to the sign-in error screen.

### Routing

* Use the browser history router. Telegram passes launch parameters in the URL fragment, which a hash router would overwrite.
* The native BackButton follows the router (see section 5).

### Configuration values

* Limits (text lengths, tag counts) and lists (countries, time zones, categories, roles, fields) come from the API (`/profiles/form-config`, `/taxonomy/*`). Do not keep copies in the frontend, so the two sides cannot drift.
* Frontend-only tunables (debounce delays, scroll thresholds) live in `shared/config`, not inside a component.

### Environment and secrets

* `VITE_*` variables are public: they end up in the bundle. A secret never goes there. The bot token and any other secret stay on the backend.
* Variables are validated once at startup in `shared/config/env.ts`; a missing or malformed value fails with a clear message.
* The mock layer (MSW and the Telegram mock environment) is imported dynamically and only under `import.meta.env.DEV`. It must not reach a production bundle.

### Mappers

Use directional names, like the backend: `mapXToY`.

## 2. Code style

* Follow the style of the surrounding code. Use clear and explicit names. Keep components and functions focused.
* TypeScript is strict. No `any`, no non-null assertion (`!`), no `as` cast except at an API boundary with a comment saying why. Use `unknown` and narrow it.
* No `enum`. Use string unions or `as const` objects.
* Named exports only. Default exports only where a tool requires them (config files).
* Do not introduce abstractions, helpers, hooks or patterns without a real need. Do not split a few repeated lines into tiny helpers; inline them.
* In a module, place private helpers above the public code that uses them.
* `useEffect` is for syncing with something outside React (Telegram events, timers, subscriptions). Not for deriving data and not for fetching. Do not memoize by default; add `useMemo` or `useCallback` only for a measured reason.
* All user-visible text comes from the dictionary in `shared/i18n`. No string literals in JSX for visible text.
* Text written by other users (tags, `bio`, goals, names) is rendered as plain text only. `dangerouslySetInnerHTML` is banned by lint. Tags are not moderated until backend stage 8.
* Do not add comments or docstrings unless they explain something non-obvious. Write all comments in English, regardless of the language used in chat or commit messages. The UI language is Russian.
* Do not rewrite working code without a reason related to the current task.
* Prefer simple, readable solutions over clever ones. Clarity beats brevity.

## 3. Execution and validation

Claude must **never run commands, tests, linters, formatters, type checkers, builds, `npm install`, dev servers or other project operations on its own**.

After making changes:

* briefly explain what was changed;
* provide the exact commands the user should run to test or verify the changes manually;
* do not claim that the changes were tested or verified unless the user ran the commands and provided the results.

### Files

* Read files only with the dedicated read tool and change them only with the edit/write tools. Do not read or modify files through shell commands, and do not rewrite several files with one script: every change is its own visible edit.
* Do not hand-edit generated files (`schema.d.ts`, `package-lock.json`).

### Git

* Work on `feat/frontend-<stage>`, one branch and one PR per stage. Never push to `main`.
* Commit or push only when the user asks. Split the work into logical commits (config, shared, feature, tests, docs), each one buildable on its own.
* Commit messages follow the repository style (`feat(frontend): ...`, `fix(frontend): ...`, `docs(frontend): ...`, `test(frontend): ...`, `refactor(frontend): ...`): a short subject, and a bullet list when it helps.
* **No `Co-Authored-By` trailer and no other attribution line** ("Generated with ...") in commit messages or pull request descriptions.
* **Every commit is signed.** The author's SSH signing key is registered on GitHub as a signing key, `commit.gpgsign` is on, and the commit shows "Verified". Check with `git log --show-signature -1` (or the badge on GitHub) before pushing. An unsigned commit is not pushed; fix it with `git rebase --exec 'git commit --amend --no-edit -S' <base>` while it is still local.
* Do not leave comments in a PR on the user's behalf. After review fixes, only re-request review.

## 4. Tests

* Tests run on Vitest with Testing Library. The network is replaced by MSW at the boundary only; do not mock our own modules to make a test pass.
* Query like a user: by role, label and visible text. Do not assert implementation details, and do not snapshot whole pages.
* Cover every state of a screen: loading, empty, error and success. For gating logic, every branch.
* A bug fix comes with a test that fails on the old code. Do not write tests that restate the implementation.
* Handlers and fixtures mirror the real contract: the same status codes and `detail` shapes as the backend. Build fixtures from the generated types, so a contract change breaks the build instead of passing silently.
* Every external-input path is tested with hostile input: an unexpected `detail` shape, a missing field, a wrong type, an empty list, a huge string, non-JSON. The result is a normalized `ApiError` or a handled empty state, never an unhandled exception.
* The Telegram wrapper is tested both inside (mocked environment) and outside Telegram.
* Auth behavior (expired token, re-authentication, ban) is tested through the real client against MSW, not only against a hook.

## 5. Telegram Mini App rules

* Colors come from the Telegram theme (`--tg-theme-*`) through the tokens in `app/styles/tokens.css`. Never hardcode a color outside the tokens. The brand accent `#10b981` is an accent only. `prefers-color-scheme` is a fallback outside Telegram, not the source of the theme. Check every screen in both light and dark.
* Never use `100vh`. Pin bottom elements to `--tg-viewport-stable-height`. Respect `--tg-safe-area-inset-*` and `--tg-content-safe-area-inset-*`.
* Use the native BackButton bound to the router, with one navigation stack. Do not draw a second back arrow. On the first page and on a deep-link entry it closes the app.
* Check API support before calling a method (BackButton 6.1, haptics 6.1, closing confirmation 6.2, vertical swipe control 7.7, safe areas and fullscreen 8.0) and keep a fallback for older clients.
* Enable closing confirmation on the profile form so entered data is not lost. Disable vertical swipes on screens with their own gestures.
* Open a chat with `openTelegramLink` (`https://t.me/<username>`).
* Never trust `initDataUnsafe` for decisions. Raw `initData` goes to the backend, which validates it.
* Keep the first bundle small: lazy-load routes, show a skeleton until the SDK is ready. Animate only `transform` and `opacity`, and reduce effects on low-end devices.
* Telegram Web hides WebView bugs. Before a release, check iOS, Android and one desktop client.

## 6. UI states and accessibility

* Every screen defines loading (skeleton), empty, error (with a retry) and offline states before it is considered done.
* Every input has a visible label or an `aria-label`; every image has an `alt`. Interactive elements are reachable by keyboard and have an accessible name. Touch targets are at least 44 px.
* Do not convey meaning by color alone (the grey "viewed" card also carries a text or icon cue). Contrast is checked on both themes.
* Respect `prefers-reduced-motion`.
