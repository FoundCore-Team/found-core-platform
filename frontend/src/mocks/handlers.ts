import { http, HttpResponse } from 'msw';

import type { Schemas } from '../shared/api';
import { createAuthResponse } from './fixtures';
import type { MockScenario } from './scenarios';

function authResponseFor(scenario: MockScenario): Schemas['TelegramAuthResponse'] {
  switch (scenario) {
    case 'banned':
      return createAuthResponse({
        user: { is_banned: true, ban_reason: 'Нарушение правил сообщества' },
      });
    case 'new':
      return createAuthResponse({ isNewUser: true });
    case 'ok':
    case 'error':
      return createAuthResponse();
  }
}

export function createHandlers(scenario: MockScenario) {
  // The `error` scenario fails the first sign-in only, so "Повторить" visibly works.
  let failuresLeft = scenario === 'error' ? 1 : 0;

  return [
    http.post('*/api/v1/auth/telegram', () => {
      if (failuresLeft > 0) {
        failuresLeft -= 1;
        return HttpResponse.json({ detail: 'Internal server error' }, { status: 500 });
      }
      return HttpResponse.json(authResponseFor(scenario));
    }),
  ];
}
