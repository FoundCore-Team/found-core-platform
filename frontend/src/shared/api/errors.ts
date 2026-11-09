export type ApiErrorKind = 'network' | 'http' | 'coded' | 'validation' | 'invalid_response';

interface ApiErrorInit {
  kind: ApiErrorKind;
  status: number | null;
  code: string | null;
  message: string;
  detail: unknown;
}

// The message goes to logs; a hostile server must not be able to make it huge.
const MAX_MESSAGE_LENGTH = 300;

export class ApiError extends Error {
  readonly kind: ApiErrorKind;
  readonly status: number | null;
  readonly code: string | null;
  readonly detail: unknown;

  constructor(init: ApiErrorInit) {
    super(init.message.slice(0, MAX_MESSAGE_LENGTH));
    this.name = 'ApiError';
    this.kind = init.kind;
    this.status = init.status;
    this.code = init.code;
    this.detail = init.detail;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value);
}

function validationMessage(items: readonly unknown[]): string {
  const first = items[0];
  if (isRecord(first) && typeof first.msg === 'string') {
    return first.msg;
  }
  return 'Request validation failed';
}

export function networkError(cause: unknown): ApiError {
  return new ApiError({
    kind: 'network',
    status: null,
    code: null,
    message: 'Network request failed',
    detail: cause,
  });
}

/** `status` is null when the body of a successful response could not be read. */
export function invalidResponse(status: number | null, body: unknown): ApiError {
  const where = status === null ? '' : ` (HTTP ${String(status)})`;
  return new ApiError({
    kind: 'invalid_response',
    status,
    code: null,
    message: `Unexpected response from the server${where}`,
    detail: body,
  });
}

/**
 * Turns an error response into an ApiError. The backend `detail` has four shapes (a string,
 * `{code, message, ...}`, `{message, profile}` and a list for 422); anything else, including a
 * body that is not JSON (`undefined`), becomes `invalid_response` instead of an exception.
 */
export function normalizeApiError(status: number, body: unknown): ApiError {
  const detail = isRecord(body) ? body.detail : undefined;

  if (typeof detail === 'string') {
    return new ApiError({ kind: 'http', status, code: null, message: detail, detail });
  }

  if (Array.isArray(detail)) {
    const message = validationMessage(detail);
    return new ApiError({ kind: 'validation', status, code: null, message, detail });
  }

  if (isRecord(detail)) {
    const { code, message } = detail;
    if (typeof code === 'string') {
      const text = typeof message === 'string' ? message : code;
      return new ApiError({ kind: 'coded', status, code, message: text, detail });
    }
    if (typeof message === 'string') {
      // The duplicate profile answer, `{message, profile}`, has no code.
      return new ApiError({ kind: 'http', status, code: null, message, detail });
    }
  }

  return invalidResponse(status, body);
}

/** A `403` with a string `detail` means the user is banned; the detail is the ban reason. */
export function getBanReason(error: ApiError): string | null {
  return error.status === 403 && typeof error.detail === 'string' ? error.detail : null;
}
