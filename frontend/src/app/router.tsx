import { useState } from 'react';
import { createBrowserRouter } from 'react-router';
import { RouterProvider } from 'react-router/dom';

import { HomePlaceholder, useSession } from '../features/auth';

function Home() {
  const session = useSession();
  return session.status === 'signed-in' ? <HomePlaceholder name={session.user.first_name} /> : null;
}

// History mode: Telegram passes launch parameters in the URL fragment, which a hash router would overwrite.
function createAppRouter() {
  return createBrowserRouter([{ path: '/', element: <Home /> }]);
}

export function AppRouter() {
  const [router] = useState(createAppRouter);
  return <RouterProvider router={router} />;
}
