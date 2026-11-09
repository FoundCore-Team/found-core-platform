import { describe, expect, it } from 'vitest';

import { EnvError, parseEnv } from './env';

describe('parseEnv', () => {
  it('uses defaults when nothing is set', () => {
    expect(parseEnv({}, true)).toEqual({
      apiOrigin: '',
      enableMocks: false,
      devInitData: undefined,
    });
  });

  it('treats empty and blank values as not set', () => {
    const env = parseEnv(
      { VITE_API_ORIGIN: '', VITE_ENABLE_MOCKS: '  ', VITE_DEV_INIT_DATA: '' },
      true,
    );

    expect(env).toEqual({ apiOrigin: '', enableMocks: false, devInitData: undefined });
  });

  it.each([
    ['https://api.example.com', 'https://api.example.com'],
    ['https://api.example.com/', 'https://api.example.com'],
    ['http://localhost:8000', 'http://localhost:8000'],
  ])('accepts the API origin %j', (value, expected) => {
    expect(parseEnv({ VITE_API_ORIGIN: value }, true).apiOrigin).toBe(expected);
  });

  it.each([
    ['/api/v1'],
    ['//evil.example.com'],
    ['https://api.example.com/api/v1'],
    ['https://api.example.com?x=1'],
    ['https://user:secret@api.example.com'],
    ['ftp://example.com'],
    ['not a url'],
  ])('rejects the API origin %j and names the variable', (value) => {
    expect(() => parseEnv({ VITE_API_ORIGIN: value }, true)).toThrow(EnvError);
    expect(() => parseEnv({ VITE_API_ORIGIN: value }, true)).toThrow(/VITE_API_ORIGIN/);
  });

  it('rejects a value that is not a string', () => {
    expect(() => parseEnv({ VITE_API_ORIGIN: 42 }, true)).toThrow(/VITE_API_ORIGIN/);
  });

  it('rejects a mocks flag other than true or false', () => {
    expect(() => parseEnv({ VITE_ENABLE_MOCKS: 'yes' }, true)).toThrow(/VITE_ENABLE_MOCKS/);
  });

  it('honors the mocks flag and the init data in development', () => {
    const env = parseEnv({ VITE_ENABLE_MOCKS: 'true', VITE_DEV_INIT_DATA: 'query_id=1' }, true);

    expect(env.enableMocks).toBe(true);
    expect(env.devInitData).toBe('query_id=1');
  });

  it('ignores the mocks flag and the init data outside development', () => {
    const env = parseEnv({ VITE_ENABLE_MOCKS: 'true', VITE_DEV_INIT_DATA: 'query_id=1' }, false);

    expect(env.enableMocks).toBe(false);
    expect(env.devInitData).toBeUndefined();
  });
});
