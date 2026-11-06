import { useSyncExternalStore } from 'react';

import { getSessionState, subscribeToSession, type SessionState } from './session';

export function useSession(): SessionState {
  return useSyncExternalStore(subscribeToSession, getSessionState);
}
