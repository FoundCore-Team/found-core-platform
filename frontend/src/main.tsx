import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';

import { App } from './app/App';
import { connectApiToSession } from './app/connectApi';
import { Providers } from './app/providers';
import './app/styles/tokens.css';
import './app/styles/global.css';
import { env } from './shared/config';
import { initTelegram } from './shared/telegram';

async function start(): Promise<void> {
  const container = document.getElementById('root');
  if (!container) {
    throw new Error('Root element #root is missing from index.html');
  }

  // The mocks are loaded only in development, so they never reach a production bundle.
  if (import.meta.env.DEV && env.enableMocks) {
    const { startMocks } = await import('./mocks');
    await startMocks();
  }

  initTelegram();
  connectApiToSession();

  createRoot(container).render(
    <StrictMode>
      <Providers>
        <App />
      </Providers>
    </StrictMode>,
  );
}

void start();
