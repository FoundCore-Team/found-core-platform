import { render, screen } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '../../tests/server';
import { resetSession } from '../features/auth';
import { createHandlers } from '../mocks/handlers';
import { createAuthResponse } from '../mocks/fixtures';
import { createMockInitData, installMockTelegramEnv } from '../shared/telegram/mockEnv';
import { App } from './App';
import { Providers } from './providers';

function renderApp() {
  render(
    <Providers>
      <App />
    </Providers>,
  );
}

afterEach(() => {
  resetSession();
  sessionStorage.clear();
});

describe('inside Telegram', () => {
  beforeEach(() => {
    installMockTelegramEnv(createMockInitData({ id: 1, first_name: 'Ada' }));
  });

  it('shows a loading state and then greets the user by name', async () => {
    server.use(...createHandlers('ok'));

    renderApp();

    expect(screen.getByRole('status')).toHaveTextContent('Загрузка');
    expect(await screen.findByRole('heading', { name: 'Привет, Ада!' })).toBeInTheDocument();
  });

  it('greets a new user too', async () => {
    server.use(...createHandlers('new'));

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Привет, Ада!' })).toBeInTheDocument();
  });

  it('shows the ban screen with the reason', async () => {
    server.use(...createHandlers('banned'));

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Доступ ограничен' })).toBeInTheDocument();
    expect(screen.getByText('Нарушение правил сообщества')).toBeInTheDocument();
  });

  it('shows the ban screen without a reason when the backend gives none', async () => {
    server.use(
      http.post('*/api/v1/auth/telegram', () =>
        HttpResponse.json(createAuthResponse({ user: { is_banned: true } })),
      ),
    );

    renderApp();

    expect(await screen.findByText('Причина не указана.')).toBeInTheDocument();
  });

  it('shows an error with a retry button that signs in again', async () => {
    server.use(...createHandlers('error'));
    const user = userEvent.setup();

    renderApp();

    expect(await screen.findByRole('heading', { name: 'Не удалось войти' })).toBeInTheDocument();
    await user.click(screen.getByRole('button', { name: 'Повторить' }));
    expect(await screen.findByRole('heading', { name: 'Привет, Ада!' })).toBeInTheDocument();
  });
});

describe('outside Telegram', () => {
  it('asks to open the app from Telegram and sends no sign-in request', async () => {
    let requests = 0;
    server.use(
      http.post('*/api/v1/auth/telegram', () => {
        requests += 1;
        return HttpResponse.json(createAuthResponse());
      }),
    );

    renderApp();

    expect(
      await screen.findByRole('heading', { name: 'Откройте из Telegram' }),
    ).toBeInTheDocument();
    expect(requests).toBe(0);
  });
});
