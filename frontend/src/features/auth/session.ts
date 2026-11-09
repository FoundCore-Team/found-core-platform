import { api, unwrap, type Schemas } from '../../shared/api';
import { getRawInitData, NotInTelegramError } from '../../shared/telegram';

export type SessionState =
  | { status: 'idle' }
  | { status: 'signing-in' }
  | { status: 'not-in-telegram' }
  | { status: 'banned'; reason: string | null }
  | { status: 'error'; error: unknown }
  | {
      status: 'signed-in';
      token: string;
      user: Schemas['UserPublicSchema'];
      profiles: Schemas['ProfileSchema'][];
      isNewUser: boolean;
    };

// The token lives in memory only: Telegram clients may wipe browser storage, and a stored token
// would outlive the session.
let state: SessionState = { status: 'idle' };
let pendingSignIn: Promise<void> | null = null;
const listeners = new Set<() => void>();

function setState(next: SessionState): void {
  state = next;
  listeners.forEach((listener) => {
    listener();
  });
}

async function requestSession(): Promise<SessionState> {
  const data = await unwrap(
    api.POST('/api/v1/auth/telegram', { body: { init_data: getRawInitData() } }),
  );
  if (data.user.is_banned) {
    return { status: 'banned', reason: data.user.ban_reason };
  }
  return {
    status: 'signed-in',
    token: data.access_token,
    user: data.user,
    profiles: data.profiles,
    isNewUser: data.is_new_user,
  };
}

async function runSignIn(): Promise<void> {
  setState({ status: 'signing-in' });
  try {
    setState(await requestSession());
  } catch (cause) {
    setState(
      cause instanceof NotInTelegramError
        ? { status: 'not-in-telegram' }
        : { status: 'error', error: cause },
    );
  }
}

export function getSessionState(): SessionState {
  return state;
}

export function subscribeToSession(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

export function getAccessToken(): string | null {
  return state.status === 'signed-in' ? state.token : null;
}

/** Signs in with the raw init data. Calls made while a sign-in runs share it. Never rejects. */
export function signIn(): Promise<void> {
  pendingSignIn ??= runSignIn().finally(() => {
    pendingSignIn = null;
  });
  return pendingSignIn;
}

/**
 * Signs in again behind the screen, when the token expired. It does not touch the state while it
 * runs, so the app does not flash a loading screen, and it rejects when the sign-in fails.
 */
export async function refreshSession(): Promise<void> {
  setState(await requestSession());
}

export function markBanned(reason: string): void {
  setState({ status: 'banned', reason });
}

/** Forgets the session. Used by tests. */
export function resetSession(): void {
  pendingSignIn = null;
  setState({ status: 'idle' });
}
