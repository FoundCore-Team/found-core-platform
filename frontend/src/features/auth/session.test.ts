import { delay, http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';

import { server } from '../../../tests/server';
import { createAuthResponse } from '../../mocks/fixtures';
import { createMockInitData, installMockTelegramEnv } from '../../shared/telegram/mockEnv';
import {
  getAccessToken,
  getSessionState,
  markBanned,
  refreshSession,
  resetSession,
  signIn,
  subscribeToSession,
} from './session';

const SIGN_IN_URL = '*/api/v1/auth/telegram';

afterEach(() => {
  resetSession();
  sessionStorage.clear();
});

describe('outside Telegram', () => {
  it('does not sign in and reports that the app is not inside Telegram', async () => {
    let requests = 0;
    server.use(
      http.post(SIGN_IN_URL, () => {
        requests += 1;
        return HttpResponse.json(createAuthResponse());
      }),
    );

    await signIn();

    expect(getSessionState()).toEqual({ status: 'not-in-telegram' });
    expect(requests).toBe(0);
  });
});

describe('inside Telegram', () => {
  beforeEach(() => {
    installMockTelegramEnv(createMockInitData({ id: 1, first_name: 'Ada' }));
  });

  it('shares one request between sign-in calls made while one is running', async () => {
    let requests = 0;
    server.use(
      http.post(SIGN_IN_URL, async () => {
        requests += 1;
        await delay(10);
        return HttpResponse.json(createAuthResponse({ accessToken: 'token-1' }));
      }),
    );

    const first = signIn();
    const second = signIn();
    await Promise.all([first, second]);

    expect(second).toBe(first);
    expect(requests).toBe(1);
    expect(getSessionState()).toMatchObject({ status: 'signed-in', token: 'token-1' });
    expect(getAccessToken()).toBe('token-1');
  });

  it('shows a banned user as banned and keeps no token', async () => {
    server.use(
      http.post(SIGN_IN_URL, () =>
        HttpResponse.json(createAuthResponse({ user: { is_banned: true, ban_reason: 'Spam' } })),
      ),
    );

    await signIn();

    expect(getSessionState()).toEqual({ status: 'banned', reason: 'Spam' });
    expect(getAccessToken()).toBeNull();
  });

  it('reports an error for a failed sign-in and signs in on a retry', async () => {
    let requests = 0;
    server.use(
      http.post(SIGN_IN_URL, () => {
        requests += 1;
        return requests === 1
          ? HttpResponse.json({ detail: 'Internal server error' }, { status: 500 })
          : HttpResponse.json(createAuthResponse());
      }),
    );

    await signIn();
    expect(getSessionState().status).toBe('error');

    await signIn();
    expect(getSessionState().status).toBe('signed-in');
    expect(requests).toBe(2);
  });

  it('replaces the token behind the screen when it is refreshed', async () => {
    let issued = 0;
    server.use(
      http.post(SIGN_IN_URL, () => {
        issued += 1;
        return HttpResponse.json(createAuthResponse({ accessToken: `token-${String(issued)}` }));
      }),
    );
    await signIn();
    const statuses: string[] = [];
    const unsubscribe = subscribeToSession(() => {
      statuses.push(getSessionState().status);
    });

    await refreshSession();
    unsubscribe();

    expect(getAccessToken()).toBe('token-2');
    expect(statuses).toEqual(['signed-in']);
  });

  it('rejects a failed refresh and keeps the session as it was', async () => {
    server.use(http.post(SIGN_IN_URL, () => HttpResponse.json(createAuthResponse())));
    await signIn();
    server.use(
      http.post(SIGN_IN_URL, () =>
        HttpResponse.json({ detail: 'Invalid init data' }, { status: 401 }),
      ),
    );

    await expect(refreshSession()).rejects.toMatchObject({ status: 401 });
    expect(getSessionState().status).toBe('signed-in');
  });

  it('switches to the banned state on demand', () => {
    markBanned('Spam');

    expect(getSessionState()).toEqual({ status: 'banned', reason: 'Spam' });
  });
});
