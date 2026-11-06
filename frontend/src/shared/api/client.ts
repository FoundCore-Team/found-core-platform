import createClient from 'openapi-fetch';

import { env } from '../config';
import { ApiError, getBanReason, invalidResponse, networkError, normalizeApiError } from './errors';
import type { paths } from './schema';

/**
 * What the API client needs from the session. The session lives in `features/auth`, which `shared`
 * must not import, so `app/` injects these through `configureAuth`.
 */
export interface AuthHooks {
  getToken: () => string | null;
  /** Signs in again with fresh init data. It rejects when the sign-in fails. */
  reauthenticate: () => Promise<void>;
  onBanned: (reason: string) => void;
}

const SIGN_IN_PATH = '/api/v1/auth/telegram' satisfies keyof paths;

let hooks: AuthHooks | null = null;
let pendingReauth: Promise<void> | null = null;

export function configureAuth(next: AuthHooks | null): void {
  hooks = next;
  pendingReauth = null;
}

// Parallel requests that hit a 401 share one sign-in.
function reauthenticateOnce(current: AuthHooks): Promise<void> {
  pendingReauth ??= current.reauthenticate().finally(() => {
    pendingReauth = null;
  });
  return pendingReauth;
}

function send(request: Request, token: string | null): Promise<Response> {
  if (token !== null) {
    request.headers.set('Authorization', `Bearer ${token}`);
  }
  return fetch(request);
}

// A `403` with a string `detail` means the user is banned; the detail is the reason.
async function reportBan(response: Response, current: AuthHooks): Promise<Response> {
  if (response.status === 403) {
    const body: unknown = await response
      .clone()
      .json()
      .catch(() => undefined);
    const reason = getBanReason(normalizeApiError(403, body));
    if (reason !== null) {
      current.onBanned(reason);
    }
  }
  return response;
}

async function authedFetch(request: Request): Promise<Response> {
  const current = hooks;
  if (current === null || new URL(request.url).pathname === SIGN_IN_PATH) {
    return fetch(request);
  }

  // The body can be read once, so keep a copy for the single retry.
  const retry = request.clone();
  const sentWith = current.getToken();
  const first = await send(request, sentWith);
  if (first.status !== 401) {
    return reportBan(first, current);
  }

  // Another request may already have signed in again while this one was in flight.
  if (current.getToken() === sentWith) {
    await reauthenticateOnce(current);
  }
  return reportBan(await send(retry, current.getToken()), current);
}

export function createApiClient(origin: string) {
  return createClient<paths>({ baseUrl: origin, fetch: authedFetch });
}

// An absolute origin: relative URLs are not valid outside a browser (tests run in Node).
export const api = createApiClient(env.apiOrigin === '' ? window.location.origin : env.apiOrigin);

interface FetchResult<T> {
  data?: T;
  error?: unknown;
  response: Response;
}

/** Resolves with the data of a successful call and throws an ApiError for every failure. */
export async function unwrap<T>(call: Promise<FetchResult<T>>): Promise<T> {
  let result: FetchResult<T>;
  try {
    result = await call;
  } catch (cause) {
    if (cause instanceof ApiError) {
      throw cause;
    }
    // A body that is not JSON on a successful response reaches us as a SyntaxError.
    throw cause instanceof SyntaxError ? invalidResponse(null, undefined) : networkError(cause);
  }
  if (result.error !== undefined) {
    throw normalizeApiError(result.response.status, result.error);
  }
  if (result.data === undefined) {
    throw invalidResponse(result.response.status, undefined);
  }
  return result.data;
}
