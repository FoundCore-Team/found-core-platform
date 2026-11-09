import type { components } from './schema';

export { api, configureAuth, createApiClient, unwrap, type AuthHooks } from './client';
export {
  ApiError,
  getBanReason,
  invalidResponse,
  networkError,
  normalizeApiError,
  type ApiErrorKind,
} from './errors';

export type Schemas = components['schemas'];
