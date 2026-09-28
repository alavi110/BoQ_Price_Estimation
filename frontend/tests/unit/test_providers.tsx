/**
 * The QueryClient provider, and what it promises the dashboard (T105).
 *
 * Every other dashboard test wraps the component under test in its own
 * `QueryClientProvider` - correctly, since an isolated test wants its own cache
 * and its own `retry: false`. That habit is exactly what hid a real bug: no test
 * ever mounted the app the way it is served, so the suite was green while
 * `next build` failed with `No QueryClient set, use QueryClientProvider to set
 * one`. The application had no provider anywhere in its tree.
 *
 * A missing provider fails at *runtime*, on the first page load, and only in the
 * browser. It is invisible to a component test that supplies its own, and it is
 * invisible to a type check, because `useQuery` is typed to be callable from
 * anywhere - it only discovers the missing provider when it renders. So the test
 * has to use the application's own provider rather than a test-built one.
 *
 * What this does and does not cover: it covers that `Providers` works and that the
 * dashboard renders under it with no test-side wiring. It does not cover that
 * `layout.tsx` actually mounts `Providers` - a layout renders `<html>`, which
 * jsdom cannot host inside a container, so that wiring is checked by `next build`
 * succeeding, not by a unit test. Claiming otherwise here would be a test that
 * looks thorough and is not.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor } from '@testing-library/react';
import { useQuery, useQueryClient } from '@tanstack/react-query';

import { Providers } from '@/components/providers';
import { request } from '@/lib/api/client';
import { Dashboard } from '@/app/(dashboard)/dashboard';

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
});

/**
 * A query that runs unconditionally, so the retry policy is observable.
 *
 * The dashboard's own queries are gated on a completed analysis, which means a
 * dashboard mounted with no upload in progress never fetches. That is correct
 * behaviour, and it is why a dashboard-only test cannot say anything about the
 * retry policy. This component issues real requests.
 *
 * It goes through the application's own `request`, not a bare `fetch`, and that is
 * the whole point. The retry predicate distinguishes "the server answered" from
 * "the request never arrived" by testing for an `ApiError` with a status - and
 * only `request` throws one. A test that called `fetch` directly would hand the
 * predicate a bare `TypeError` for every case, the predicate could not tell them
 * apart, and the test would pass no matter what the policy was.
 */
function Probe() {
  const query = useQuery({
    queryKey: ['probe'],
    queryFn: () => request<string>('/probe'),
  });
  return <p role="status">{typeof query.data === 'string' ? query.data : 'loading'}</p>;
}

/**
 * Reports the identity of the client it is given, once per render.
 *
 * Used instead of counting fetches to test client stability. Inferring stability
 * from request counts does not work: React Query holds its cache on the client
 * instance, and swapping the instance underneath a mounted observer is
 * handled by a re-subscribe whose timing is not something a test should be
 * asserting on. The identity of the client is the actual promise - one client per
 * mount - and it can be checked directly.
 */
function ClientIdentity({ onClient }: { onClient: (client: unknown) => void }) {
  onClient(useQueryClient());
  return null;
}

function okJson(body: unknown) {
  return { ok: true, status: 200, text: async () => JSON.stringify(body) };
}

describe('the application supplies its own QueryClient', () => {
  it('renders the dashboard with no test-built provider', () => {
    // The dashboard's queries are gated, so nothing is fetched before an upload.
    // The point is that `useQuery` is reached at all - it throws during render,
    // before any network call, if no provider sits above it.
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(okJson({})));

    render(
      <Providers>
        <Dashboard />
      </Providers>
    );

    expect(screen.getByRole('main')).toBeInTheDocument();
    expect(screen.getByRole('heading', { level: 1 })).toHaveTextContent('داشبورد');
  });

  it('hands every consumer the same client for the life of the mount', () => {
    /*
     * The client is created in a `useState` initialiser precisely so it survives
     * re-renders. If it moved to a plain `new QueryClient()` in the render body,
     * every render would hand the tree a different client with an empty cache -
     * and on this dashboard that means the analysis restarts and the user loses
     * their place in the flow.
     *
     * Checked by identity across a re-render, and the render count is asserted
     * too. Without that, a `Providers` that somehow rendered its children only
     * once would pass while having proved nothing - and a child that never
     * re-renders is exactly the case where a rebuilt client would go unnoticed.
     */
    const seen: unknown[] = [];
    const { rerender } = render(
      <Providers>
        <ClientIdentity onClient={(client) => seen.push(client)} />
      </Providers>
    );
    const beforeRerender = seen.length;
    expect(beforeRerender).toBeGreaterThan(0);

    rerender(
      <Providers>
        <ClientIdentity onClient={(client) => seen.push(client)} />
      </Providers>
    );

    // The child did re-render, so the identity comparison is meaningful.
    expect(seen.length).toBeGreaterThan(beforeRerender);
    expect(new Set(seen).size).toBe(1);
  });

  it('gives two separate mounts their own clients, so one user cannot read another cache', () => {
    /*
     * The counterpart to the test above, and the reason this is a `useState`
     * initialiser rather than a module-level `const`. A client at module scope is
     * created once per JavaScript realm - which, on the server, is shared across
     * every request. Two users would then share one cache, and the second to open
     * the dashboard could be served the first one's BoQ.
     */
    const seen: unknown[] = [];
    const record = (client: unknown) => seen.push(client);

    render(
      <Providers>
        <ClientIdentity onClient={record} />
      </Providers>
    );
    render(
      <Providers>
        <ClientIdentity onClient={record} />
      </Providers>
    );

    expect(seen.length).toBeGreaterThanOrEqual(2);
    expect(seen[0]).not.toBe(seen[seen.length - 1]);
  });

  it('does not retry a failure the server actually answered', async () => {
    /*
     * A 403 means the user's session is not valid for this BoQ. Retrying it turns
     * a refusal the user can act on - log in again - into a five-second spinner
     * followed by the same refusal, and the dashboard gives them no way to tell a
     * slow network from a rejected request.
     */
    const fetchMock = vi.fn().mockResolvedValue({
      ok: false,
      status: 403,
      text: async () => JSON.stringify({ detail: 'دسترسی ندارید' }),
    });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <Providers>
        <Probe />
      </Providers>
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalled());
    // Longer than the provider's 1s `retryDelay`, so a retry would have fired.
    await new Promise((resolve) => setTimeout(resolve, 1200));

    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it('retries once a request that never reached the server', async () => {
    /*
     * The counterpart, and the reason the retry predicate tests the status rather
     * than the error type. A dropped connection or a DNS failure has no status at
     * all - the request never got an answer - and a single retry turns a
     * transient blip into a success the user never notices.
     */
    // The rejection is a bare `TypeError`, which is what the browser actually
    // raises when a request never completes - not a pre-built `ApiError`. Using
    // the real failure keeps the test honest about what the predicate sees.
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValue({ ok: true, status: 200, text: async () => '"v"' });
    vi.stubGlobal('fetch', fetchMock);

    render(
      <Providers>
        <Probe />
      </Providers>
    );

    // The timeout exceeds the provider's 1s `retryDelay` deliberately: waiting
    // less would race the retry and this test would pass or fail depending on
    // whether the timer happened to fire first.
    await waitFor(() => expect(screen.getByRole('status')).toHaveTextContent('v'), {
      timeout: 3000,
    });
    expect(fetchMock).toHaveBeenCalledTimes(2);
  });
});
