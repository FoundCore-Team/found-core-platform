export class EnvError extends Error {
  constructor(message: string) {
    super(message);
    this.name = 'EnvError';
  }
}

export interface Env {
  /** Origin of the API, or '' for the same origin. The `/api/v1` prefix is part of the typed paths. */
  apiOrigin: string;
  enableMocks: boolean;
  devInitData: string | undefined;
}

function readString(raw: Readonly<Record<string, unknown>>, key: string): string | undefined {
  const value = raw[key];
  if (value === undefined) {
    return undefined;
  }
  if (typeof value !== 'string') {
    throw new EnvError(`${key} must be a string`);
  }
  const trimmed = value.trim();
  return trimmed === '' ? undefined : trimmed;
}

function parseApiOrigin(value: string | undefined): string {
  if (value === undefined) {
    return '';
  }
  const error = new EnvError(
    `VITE_API_ORIGIN must be empty or an http(s) origin like "https://api.example.com" without a path, got "${value}"`,
  );
  let url: URL;
  try {
    url = new URL(value);
  } catch {
    throw error;
  }
  const isPlainOrigin =
    (url.protocol === 'http:' || url.protocol === 'https:') &&
    url.pathname === '/' &&
    url.search === '' &&
    url.hash === '' &&
    url.username === '' &&
    url.password === '';
  if (!isPlainOrigin) {
    throw error;
  }
  return url.origin;
}

function parseFlag(key: string, value: string | undefined): boolean {
  if (value === undefined || value === 'false') {
    return false;
  }
  if (value === 'true') {
    return true;
  }
  throw new EnvError(`${key} must be "true" or "false", got "${value}"`);
}

export function parseEnv(raw: Readonly<Record<string, unknown>>, isDev: boolean): Env {
  const mocksFlag = parseFlag('VITE_ENABLE_MOCKS', readString(raw, 'VITE_ENABLE_MOCKS'));
  const devInitData = readString(raw, 'VITE_DEV_INIT_DATA');

  return {
    apiOrigin: parseApiOrigin(readString(raw, 'VITE_API_ORIGIN')),
    // Mocks and a pasted init data must never take effect in a production build.
    enableMocks: isDev && mocksFlag,
    devInitData: isDev ? devInitData : undefined,
  };
}

export const env = parseEnv(import.meta.env, import.meta.env.DEV);
