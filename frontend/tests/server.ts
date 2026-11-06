import { setupServer } from 'msw/node';

// Handlers are added per test with `server.use(...)`.
export const server = setupServer();
