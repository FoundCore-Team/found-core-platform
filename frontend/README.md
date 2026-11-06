# FoundCore frontend

The Telegram Mini App of FoundCore. Context and architecture: [`CLAUDE.md`](CLAUDE.md). Rules: [`docs/rules.md`](docs/rules.md). Stage plans: [`docs/plans/`](docs/plans/).

## Requirements

Node 24.15 or newer (`.nvmrc`), npm. Install Node with `fnm` or `nvm`, not the system package.

## Commands

```bash
npm ci               # install from package-lock.json
npm run dev          # dev server
npm run typecheck
npm run lint
npm test
npm run build
npm run preview      # serve the production build
npm run api:types    # regenerate src/shared/api/schema.d.ts from the backend /openapi.json
```

`api:types` reads `OPENAPI_URL` and defaults to `http://localhost:8000/openapi.json`, so the backend must be running.

## Development modes

Copy `.env.example` to `.env.local` and pick one.

- **Mocks, no backend and no Telegram.** `VITE_ENABLE_MOCKS=true`. The scenario is chosen by `?mock=ok|new|banned|error`.
- **Local backend.** Run and seed the backend as described in the root `CLAUDE.md`, put the output of `scripts/dev_gen_init_data.py` into `VITE_DEV_INIT_DATA`, and leave mocks off. The data expires after 300 seconds.
- **Real Telegram.** An https tunnel to the dev or preview server and a test bot from BotFather.
