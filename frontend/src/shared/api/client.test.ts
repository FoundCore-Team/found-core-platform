import { http, HttpResponse } from 'msw';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { server } from '../../../tests/server';
import { api, configureAuth, unwrap } from './client';
import { ApiError } from './errors';

const CATEGORIES_URL = '*/api/v1/taxonomy/categories';
const SIGN_IN_URL = '*/api/v1/auth/telegram';

function getCategories() {
  return unwrap(api.GET('/api/v1/taxonomy/categories'));
}

let token: string | null;
const reauthenticate = vi.fn<() => Promise<void>>();
const onBanned = vi.fn<(reason: string) => void>();

beforeEach(() => {
  token = 'old';
  reauthenticate.mockReset();
  reauthenticate.mockImplementation(() => {
    token = 'new';
    return Promise.resolve();
  });
  onBanned.mockReset();
  configureAuth({ getToken: () => token, reauthenticate, onBanned });
});

afterEach(() => {
  configureAuth(null);
});

describe('api client', () => {
  it('sends the access token as a bearer header', async () => {
    let authorization: string | null = null;
    server.use(
      http.get(CATEGORIES_URL, ({ request }) => {
        authorization = request.headers.get('authorization');
        return HttpResponse.json([]);
      }),
    );

    await getCategories();

    expect(authorization).toBe('Bearer old');
  });

  it('signs in once for parallel 401 answers and retries each request once', async () => {
    let hits = 0;
    server.use(
      http.get(CATEGORIES_URL, ({ request }) => {
        hits += 1;
        if (request.headers.get('authorization') !== 'Bearer new') {
          return HttpResponse.json({ detail: 'Token expired' }, { status: 401 });
        }
        return HttpResponse.json([]);
      }),
    );

    const results = await Promise.all([getCategories(), getCategories(), getCategories()]);

    expect(results).toEqual([[], [], []]);
    expect(reauthenticate).toHaveBeenCalledTimes(1);
    expect(hits).toBe(6);
  });

  it('fails with the 401 when the retry is rejected too, without a second sign-in', async () => {
    let hits = 0;
    server.use(
      http.get(CATEGORIES_URL, () => {
        hits += 1;
        return HttpResponse.json({ detail: 'Token expired' }, { status: 401 });
      }),
    );

    await expect(getCategories()).rejects.toMatchObject({ kind: 'http', status: 401 });
    expect(reauthenticate).toHaveBeenCalledTimes(1);
    expect(hits).toBe(2);
  });

  it('passes the error of a failed sign-in through', async () => {
    const failure = new ApiError({
      kind: 'http',
      status: 401,
      code: null,
      message: 'Invalid init data',
      detail: 'Invalid init data',
    });
    reauthenticate.mockRejectedValue(failure);
    server.use(
      http.get(CATEGORIES_URL, () =>
        HttpResponse.json({ detail: 'Token expired' }, { status: 401 }),
      ),
    );

    await expect(getCategories()).rejects.toBe(failure);
  });

  it('does not sign in again when the sign-in request itself gets a 401', async () => {
    server.use(
      http.post(SIGN_IN_URL, () =>
        HttpResponse.json({ detail: 'Invalid init data' }, { status: 401 }),
      ),
    );

    await expect(
      unwrap(api.POST('/api/v1/auth/telegram', { body: { init_data: 'x' } })),
    ).rejects.toMatchObject({ kind: 'http', status: 401 });
    expect(reauthenticate).not.toHaveBeenCalled();
  });

  it('reports a ban for a 403 with a string detail', async () => {
    server.use(
      http.get(CATEGORIES_URL, () => HttpResponse.json({ detail: 'Spam' }, { status: 403 })),
    );

    await expect(getCategories()).rejects.toMatchObject({ status: 403 });
    expect(onBanned).toHaveBeenCalledWith('Spam');
  });

  it('does not report a ban for a 403 with an object detail', async () => {
    server.use(
      http.get(CATEGORIES_URL, () =>
        HttpResponse.json({ detail: { code: 'forbidden', message: 'No' } }, { status: 403 }),
      ),
    );

    await expect(getCategories()).rejects.toMatchObject({ kind: 'coded', code: 'forbidden' });
    expect(onBanned).not.toHaveBeenCalled();
  });

  it('turns a network failure into an ApiError', async () => {
    server.use(http.get(CATEGORIES_URL, () => HttpResponse.error()));

    await expect(getCategories()).rejects.toMatchObject({ kind: 'network', status: null });
  });

  it('turns a non-JSON error body into invalid_response', async () => {
    server.use(
      http.get(CATEGORIES_URL, () => new HttpResponse('<html>Bad gateway</html>', { status: 502 })),
    );

    await expect(getCategories()).rejects.toMatchObject({ kind: 'invalid_response', status: 502 });
  });

  it('turns a successful answer that is not JSON into invalid_response', async () => {
    server.use(
      http.get(
        CATEGORIES_URL,
        () =>
          new HttpResponse('oops', {
            status: 200,
            headers: { 'content-type': 'application/json' },
          }),
      ),
    );

    await expect(getCategories()).rejects.toMatchObject({ kind: 'invalid_response' });
  });
});
