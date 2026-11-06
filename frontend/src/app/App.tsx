import { useEffect } from 'react';

import {
  BannedScreen,
  getSessionState,
  LoadingScreen,
  NotInTelegramScreen,
  SignInErrorScreen,
  signIn,
  useSession,
} from '../features/auth';
import { AppRouter } from './router';

export function App() {
  const session = useSession();

  useEffect(() => {
    // StrictMode runs effects twice in development; only the first run signs in.
    if (getSessionState().status === 'idle') {
      void signIn();
    }
  }, []);

  switch (session.status) {
    case 'idle':
    case 'signing-in':
      return <LoadingScreen />;
    case 'not-in-telegram':
      return <NotInTelegramScreen />;
    case 'banned':
      return <BannedScreen reason={session.reason} />;
    case 'error':
      return (
        <SignInErrorScreen
          onRetry={() => {
            void signIn();
          }}
        />
      );
    case 'signed-in':
      return <AppRouter />;
  }
}
