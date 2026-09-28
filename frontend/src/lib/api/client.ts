/**
 * The HTTP client every API call in the dashboard goes through.
 *
 * One client, three jobs, and the reason it is one file rather than an axios
 * call at each call site is error handling. The backend's failures are
 * meaningful: a 422 carries the parser's own message about the user's own
 * spreadsheet, and a 403 means the user's role is insufficient for the defense
 * document. Both need to reach the person who can act on them. An axios call that
 * throws a generic error at each site tends to either swallow the detail or show
 * a status code, and the spreadsheet parser's row-and-column message - the one
 * thing that tells an operator which cell to fix - is exactly the kind of detail
 * that gets dropped.
 *
 * The other two jobs are the base URL and the credential. The base URL is
 * resolved through the Next rewrite at `/api/*` so the browser never makes a
 * cross-origin request in development, and a token is only ever sent in the
 * `Authorization` header, never in a query string - a token in a URL is a token
 * in the browser history, the access log, and every `Referer` that follows.
 */

/** The backend's own error shape, from its FastAPI exception handlers. */
export class ApiError extends Error {
  readonly status: number;
  /** The field-level validation detail, when the server supplied one. */
  readonly detail: unknown;

  constructor(status: number, message: string, detail?: unknown) {
    super(message);
    this.name = 'ApiError';
    this.status = status;
    this.detail = detail;
  }

  /** True when the request was refused on the user's credentials, not on its content. */
  get isAuthFailure(): boolean {
    return this.status === 401 || this.status === 403;
  }

  /**
   * True when the request never reached the server.
   *
   * A `fetch` rejection is a network failure - the dev server is not up, the
   * laptop is offline, CORS blocked it. It is a different situation from a 500
   * with a message, and telling a user "the server is not reachable" when the
   * server is fine but returned an error sends them to the wrong place.
   */
  get isNetworkFailure(): boolean {
    return this.status === 0;
  }
}

/** The message to show for a transport failure, in the user's language. */
export const NETWORK_ERROR_MESSAGE =
  'ارتباط با سرور برقرار نشد. اتصال شبکه و در حال اجرا بودن سرویس را بررسی کنید.';

const TOKEN_STORAGE_KEY = 'boq.access_token';

export function readToken(): string | null {
  if (typeof window === 'undefined') {
    return null;
  }
  try {
    return window.sessionStorage.getItem(TOKEN_STORAGE_KEY);
  } catch {
    // Storage throws in a browser configured to block it, which is a private
    // mode some of the users of an on-premise tender tool will be in. An
    // unauthenticated client is degraded, not broken - the API answers 401 and
    // the user is prompted - so the failure is swallowed rather than propagated.
    return null;
  }
}

export function writeToken(token: string): void {
  try {
    window.sessionStorage.setItem(TOKEN_STORAGE_KEY, token);
  } catch {
    // As above: no storage means no persistence, and the session simply ends at
    // the tab. Failing the login outright would be worse than not remembering it.
  }
}

export function clearToken(): void {
  try {
    window.sessionStorage.removeItem(TOKEN_STORAGE_KEY);
  } catch {
    // Nothing to do - there is no storage, so there is nothing to clear.
  }
}

function extractDetail(body: unknown): string | null {
  if (typeof body === 'string') {
    return body || null;
  }
  if (body && typeof body === 'object' && 'detail' in body) {
    const { detail } = body as { detail: unknown };
    if (typeof detail === 'string') {
      return detail || null;
    }
    // FastAPI's request-validation errors put a list of {loc, msg, type} in
    // `detail`. Flattening to "body.job_id: field required" keeps the part an
    // operator can act on without rendering a raw object into the page.
    if (Array.isArray(detail)) {
      const parts = detail
        .map((entry) => {
          if (entry && typeof entry === 'object' && 'msg' in entry) {
            const { loc, msg } = entry as { loc?: unknown[]; msg?: unknown };
            const field = Array.isArray(loc) ? loc.join('.') : '';
            return field ? `${field}: ${String(msg)}` : String(msg);
          }
          return null;
        })
        .filter((part): part is string => Boolean(part));
      return parts.length ? parts.join('، ') : null;
    }
  }
  return null;
}

export interface RequestOptions {
  method?: 'GET' | 'POST' | 'PUT' | 'PATCH' | 'DELETE';
  /** A JSON-serialisable body. Sent as-is, without a FormData check. */
  body?: unknown;
  signal?: AbortSignal;
  /** A `FormData` body, for the upload. Sent without a content-type override. */
  form?: FormData;
}

/**
 * The base path every request is sent to.
 *
 * `/api` rather than an absolute `http://localhost:8000`, because
 * `next.config.js` rewrites it to the backend. Same-origin in development means
 * no CORS preflight and no cookie/SameSite conversation for the download links;
 * in production the two are served from one host behind one nginx, which is what
 * the infrastructure assumes.
 */
export const API_BASE = '/api';

export async function request<T>(path: string, options: RequestOptions = {}): Promise<T> {
  const { method = 'GET', body, signal, form } = options;

  const headers: Record<string, string> = { Accept: 'application/json' };
  if (body !== undefined) {
    headers['Content-Type'] = 'application/json';
  }
  const token = readToken();
  if (token) {
    headers.Authorization = `Bearer ${token}`;
  }

  let response: Response;
  try {
    response = await fetch(`${API_BASE}${path}`, {
      method,
      headers,
      // The body is a body either way, but the `form` branch is explicit so the
      // Content-Type header above is visibly not applied: the browser has to set
      // the multipart boundary itself, and a hand-set header without it produces
      // a request the server cannot parse.
      body: form ?? (body === undefined ? undefined : JSON.stringify(body)),
      signal,
      credentials: 'same-origin',
    });
  } catch (cause) {
    // A fetch rejection is a transport failure. Status 0 marks it as such, so
    // `ApiError.isNetworkFailure` can tell it from a 500 with a real message.
    if (cause instanceof DOMException && cause.name === 'AbortError') {
      throw cause;
    }
    throw new ApiError(0, NETWORK_ERROR_MESSAGE, cause);
  }

  /*
   * Read the body once, whichever way this `Response` exposes it.
   *
   * `text()` is the primary path because it handles a bodyless `204` without a
   * throw, which `json()` does not. The `json()` branch is a fallback for a
   * response object that only implements the JSON accessor, which is what a
   * test double is and what a service-worker or fetch shim will sometimes be -
   * and reading the body through one tolerant helper beats every call site
   * having to know which kind it was handed.
   */
  let parsed: unknown = null;
  try {
    if (typeof response.text === 'function') {
      const text = await response.text();
      if (text) {
        try {
          parsed = JSON.parse(text);
        } catch {
          // Not JSON. A proxy's HTML error page lands here, and returning the
          // markup means the caller shows something rather than "no detail".
          parsed = text;
        }
      }
    } else if (typeof response.json === 'function') {
      parsed = await response.json();
    }
  } catch {
    // A body that cannot be read is not a failed request. The status is what
    // decides success, and the detail is best-effort.
    parsed = null;
  }

  if (!response.ok) {
    throw new ApiError(
      response.status,
      extractDetail(parsed) ?? `خطای ${response.status} از سرور`,
      parsed
    );
  }

  return parsed as T;
}
