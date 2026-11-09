import { network } from 'virtual:msw';

import { createMockInitData, installMockTelegramEnv } from '../shared/telegram/mockEnv';
import { createHandlers } from './handlers';
import { readScenario } from './scenarios';

// Development only (see main.tsx): a fake Telegram environment and a fake backend, so the app
// runs in a plain browser without either.
export async function startMocks(): Promise<void> {
  installMockTelegramEnv(createMockInitData({ id: 1, first_name: 'Ада' }));
  network.configure({ handlers: createHandlers(readScenario(window.location.search)) });
  await network.enable();
}
