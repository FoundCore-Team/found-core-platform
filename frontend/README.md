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
- **Real Telegram.** An https tunnel and a test bot from BotFather (`/newbot`). Put the bot token into the backend `.env` (`APP_CONFIG__BOT__TOKEN`) and recreate the API container. Delete `frontend/.env.local` first: a production build bundles its `VITE_*` values, and in development `VITE_DEV_INIT_DATA` would override the real init data. Then:

  ```bash
  npm run build && npm run preview                  # http://localhost:4173, proxies /api to the backend
  cloudflared tunnel --url http://localhost:4173    # prints https://<random>.trycloudflare.com
  ```

  In BotFather open `/mybots`, the bot, `Bot Settings`, `Menu Button`, `Configure menu button` and send the tunnel address. It changes on every run, so repeat this step. The preview server accepts `*.trycloudflare.com` hosts (`preview.allowedHosts` in `vite.config.ts`); for another tunnel provider add its host there.
