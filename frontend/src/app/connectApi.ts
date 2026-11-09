import { getAccessToken, markBanned, refreshSession } from '../features/auth';
import { configureAuth } from '../shared/api';

// `shared` cannot import the session, so the app hands it over.
export function connectApiToSession(): void {
  configureAuth({
    getToken: getAccessToken,
    reauthenticate: refreshSession,
    onBanned: markBanned,
  });
}
