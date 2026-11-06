import { describe, expect, it } from 'vitest';

import { ApiError, getBanReason, networkError, normalizeApiError } from './errors';

describe('normalizeApiError', () => {
  it('reads a string detail', () => {
    const error = normalizeApiError(404, { detail: 'Profile not found' });

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({
      kind: 'http',
      status: 404,
      code: null,
      message: 'Profile not found',
    });
  });

  it('reads an object detail with a code and keeps the extra fields', () => {
    const detail = {
      code: 'contact_limit_reached',
      message: 'Daily limit reached',
      limit: 20,
      resets_at: '2026-11-07T10:00:00Z',
    };

    const error = normalizeApiError(429, { detail });

    expect(error).toMatchObject({ kind: 'coded', status: 429, code: 'contact_limit_reached' });
    expect(error.detail).toEqual(detail);
  });

  it('reads the duplicate profile answer, which has a message and no code', () => {
    const detail = { message: 'Profile already exists', profile: { id: 7 } };

    const error = normalizeApiError(409, { detail });

    expect(error).toMatchObject({ kind: 'http', status: 409, code: null });
    expect(error.detail).toEqual(detail);
  });

  it('reads a validation list and uses the first message', () => {
    const detail = [{ loc: ['body', 'init_data'], msg: 'Field required', type: 'missing' }];

    expect(normalizeApiError(422, { detail })).toMatchObject({
      kind: 'validation',
      status: 422,
      message: 'Field required',
    });
  });

  it('falls back to a generic message for a validation list of unexpected items', () => {
    expect(normalizeApiError(422, { detail: [1, 2] })).toMatchObject({
      kind: 'validation',
      message: 'Request validation failed',
    });
  });

  it.each([
    [undefined],
    [null],
    [42],
    ['plain text'],
    [[]],
    [{}],
    [{ error: 'x' }],
    [{ detail: null }],
    [{ detail: 42 }],
    [{ detail: {} }],
    [{ detail: { code: 1 } }],
  ])('turns the body %j into invalid_response without throwing', (body) => {
    const error = normalizeApiError(500, body);

    expect(error).toBeInstanceOf(ApiError);
    expect(error).toMatchObject({ kind: 'invalid_response', status: 500, code: null });
  });

  it('caps the message of a huge detail and keeps the detail itself', () => {
    const detail = 'x'.repeat(100_000);

    const error = normalizeApiError(400, { detail });

    expect(error.message.length).toBeLessThanOrEqual(300);
    expect(error.detail).toBe(detail);
  });
});

describe('networkError', () => {
  it('has no status and no code', () => {
    const cause = new TypeError('Failed to fetch');

    expect(networkError(cause)).toMatchObject({
      kind: 'network',
      status: null,
      code: null,
      detail: cause,
    });
  });
});

describe('getBanReason', () => {
  it('returns the reason for a 403 with a string detail', () => {
    expect(getBanReason(normalizeApiError(403, { detail: 'Spam' }))).toBe('Spam');
  });

  it('returns null for a 403 with an object detail', () => {
    expect(
      getBanReason(normalizeApiError(403, { detail: { code: 'x', message: 'y' } })),
    ).toBeNull();
  });

  it('returns null for another status', () => {
    expect(getBanReason(normalizeApiError(404, { detail: 'Not found' }))).toBeNull();
  });
});
