import type { Schemas } from '../shared/api';

type User = Schemas['UserPublicSchema'];

export function createUser(overrides: Partial<User> = {}): User {
  return {
    id: 1,
    telegram_id: 1,
    first_name: 'Ада',
    last_name: null,
    username: 'ada_dev',
    photo_url: null,
    is_admin: false,
    is_banned: false,
    ban_reason: null,
    active_profile_id: null,
    ...overrides,
  };
}

interface AuthResponseOptions {
  accessToken?: string;
  isNewUser?: boolean;
  user?: Partial<User>;
}

export function createAuthResponse({
  accessToken = 'mock-access-token',
  isNewUser = false,
  user = {},
}: AuthResponseOptions = {}): Schemas['TelegramAuthResponse'] {
  return {
    access_token: accessToken,
    token_type: 'bearer',
    is_new_user: isNewUser,
    user: createUser(user),
    profiles: [],
  };
}
