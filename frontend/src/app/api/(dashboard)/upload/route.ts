/**
 * The BoQ upload proxy.
 *
 * Every other dashboard request goes through the `/api/:path*` rewrite in
 * `next.config.js`, which is enough. The upload is the exception, and the reason
 * is the request body.
 *
 * A BoQ is up to 50 MB (`MAX_FILE_SIZE_MB` in the backend's config) of multipart
 * data. Two things go wrong if that body is relayed by the generic rewrite rather
 * than by a handler that thinks about it:
 *
 * **The body has to be a stream.** `request.body` is forwarded as a
 * `ReadableStream` here, so the bytes are relayed as they arrive. Reading the
 * body into a buffer first - which is what `await request.formData()` would do -
 * means holding a 50 MB file in the Node heap, times however many operators upload
 * at once, and the failure mode is an out-of-memory kill of the whole dashboard
 * rather than a rejected upload.
 *
 * **The `Content-Type` must be forwarded verbatim, and never re-derived.** A
 * multipart body carries its own boundary in that header, and the boundary is
 * generated per request. Reconstructing the header from a parsed `FormData` - or
 * letting a helper set `multipart/form-data` without the boundary - produces a
 * request the backend's parser rejects with a message about a malformed body,
 * which tells the operator nothing about their file.
 *
 * The honest cost is an extra hop: the file crosses this process and then the
 * backend, where a rewrite would relay it directly. It is worth one hop here
 * because it puts the streaming, the boundary handling and the transport-failure
 * normalisation for the largest request in the system in one reviewed place
 * instead of spread across framework config.
 */
import { NextResponse } from 'next/server';

const BACKEND_URL =
  process.env.BACKEND_URL || process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

/** Mirrors `MAX_FILE_SIZE_MB` in `backend/src/core/config.py`. */
const MAX_FILE_SIZE_MB = 50;

/**
 * Node, not the edge runtime.
 *
 * The edge runtime does not give the body stream in a shape that can be forwarded
 * as-is, and imposes its own body limits - which is the exact class of problem
 * this handler exists to avoid.
 */
export const runtime = 'nodejs';

/** Never cached: an upload response is a job id, unique to the request. */
export const dynamic = 'force-dynamic';

export async function POST(request: Request) {
  /*
   * The size ceiling, checked against `Content-Length` rather than by reading the
   * body - reading the body is the thing being avoided. An oversized file is
   * refused in milliseconds instead of after a long upload that was always going
   * to be rejected.
   *
   * A chunked upload with no declared length is not caught here. The backend
   * enforces the same limit while streaming, so the worst case is that the bytes
   * are relayed before the refusal arrives. Failing closed on a missing
   * `Content-Length` would be the worse trade: it rejects every browser that
   * streams, which is most of them, in exchange for catching the uncommon case.
   */
  const declaredLength = Number(request.headers.get('content-length') ?? Number.NaN);
  if (Number.isFinite(declaredLength) && declaredLength > MAX_FILE_SIZE_MB * 1024 * 1024) {
    return NextResponse.json(
      { detail: `حجم فایل بیش از حد مجاز است (${MAX_FILE_SIZE_MB} مگابایت).` },
      { status: 413 }
    );
  }

  /*
   * The extension is *not* re-checked here, and the omission is deliberate. In a
   * multipart request the filename lives inside the body, not in the request's
   * headers, so reading it would mean consuming the stream this handler exists to
   * relay. It is checked in the browser, before anything is sent, and again by the
   * backend from the filename it parses out. A third check that could only work by
   * buffering the body would trade the streaming guarantee for a duplicate.
   */

  /*
   * `Content-Type` is copied through unchanged, which carries the multipart
   * boundary with it. `Authorization` is forwarded so the backend still does the
   * authenticating: the dashboard is not a place to grant access, and a proxy that
   * dropped the credential would turn every 401 into a confusing 422.
   */
  const headers = new Headers();
  for (const header of ['content-type', 'authorization']) {
    const value = request.headers.get(header);
    if (value) {
      headers.set(header, value);
    }
  }

  let upstream: Response;
  try {
    upstream = await fetch(`${BACKEND_URL}/boq/upload`, {
      method: 'POST',
      headers,
      // The stream, not a buffer. See the module docstring.
      body: request.body,
      // Required by undici when the body is a stream, and ignored elsewhere.
      ...({ duplex: 'half' } as Record<string, string>),
    });
  } catch (cause) {
    /*
     * The backend being down is neither the operator's fault nor a 4xx, so it must
     * not be reported as one. A 502 carrying a message in the user's language is
     * the only thing that sends them to look at the server rather than at their
     * spreadsheet, and it keeps the failure from being cached as if it were an
     * answer.
     */
    console.error('[upload proxy] backend unreachable:', cause);
    return NextResponse.json(
      {
        detail:
          'ارتباط با سرور برقرار نشد. اتصال شبکه و در حال اجرا بودن سرویس را بررسی کنید.',
      },
      { status: 502 }
    );
  }

  /*
   * The upstream status and body are returned unchanged, and with the `detail`
   * shape preserved, because the parser's own message is the only text that names
   * the row and column at fault. Re-wrapping it here would discard the one part of
   * the response an operator can act on.
   */
  return new NextResponse(upstream.body, {
    status: upstream.status,
    statusText: upstream.statusText,
    headers: {
      'content-type': upstream.headers.get('content-type') ?? 'application/json',
    },
  });
}
