/**
 * The client-side context the dashboard needs, in one place.
 *
 * `'use client'` because `QueryClientProvider` holds React state and cannot be
 * rendered from a server component. It sits in the root layout so it wraps every
 * route: a provider mounted on the page instead would be re-created on every
 * navigation, which throws away the cache and restarts every in-flight query -
 * so a user who goes to the weights page and back would watch the analysis
 * re-fetch from the beginning.
 */
'use client';

import * as React from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { ApiError } from '@/lib/api/client';

/**
 * How many times a request that never reached the server is re-sent.
 *
 * One, not three. A transport failure here means the operator's connection
 * dropped, not that the server is unwell, and re-sending three times turns a
 * one-second blip into a fifteen-second wait on a page they are watching. The
 * export and override endpoints are not idempotent in effect, so a retry that
 * succeeded server-side the first time would duplicate work.
 */
const MAX_TRANSPORT_RETRIES = 1;

export function Providers({ children }: { children: React.ReactNode }) {
  /*
   * One client for the life of the tab, created lazily on first render.
   *
   * `useState` with an initialiser rather than a module-level constant or a plain
   * `useMemo`: a module-level client is shared across every request during
   * server rendering, so one user's cached BoQ could be served to another; and a
   * `useMemo` is not a guarantee React will not discard, which would silently
   * drop the cache. `useState`'s initialiser runs exactly once per mount.
   */
  const [client] = React.useState(
    () =>
      new QueryClient({
        defaultOptions: {
          queries: {
            /*
             * Failures are shown, not retried away.
             *
             * Every request here is authenticated and most are non-idempotent in
             * effect - an export, an override. A silent retry turns a 403 into a
             * five-second spinner and then the same 403, and the user cannot tell
             * a slow network from a rejected request. A network blip is worth one
             * retry; anything the server answered is the answer.
             */
            retry: (failureCount, error) => {
              if (error instanceof ApiError && error.status > 0) {
                return false;
              }
              return failureCount < MAX_TRANSPORT_RETRIES;
            },
            /*
             * Stated rather than inherited. The default is 1s then doubling, and
             * with a single retry allowed that default is really just "1s" - but
             * it is a user-visible number, so it is a decision and not something
             * to leave to whichever version is installed. A second is long enough
             * that a dropped connection has genuinely finished failing, and short
             * enough that the user does not read it as the app having hung.
             */
            retryDelay: 1000,
            /*
             * Hold the cache briefly so switching tabs and back does not refetch.
             * The analysis behind these queries does not change under the user -
             * only an explicit override or a fresh upload does, and both
             * invalidate the `boq` namespace directly - so a longer window would
             * only delay showing the user their own change.
             */
            gcTime: 5 * 60 * 1000,
            refetchOnWindowFocus: false,
            /*
             * The weight and price queries are already gated on the analysis
             * having completed, so there is nothing to poll for. A forecast is
             * re-requested when the user asks for it.
             */
            refetchOnReconnect: true,
          },
          mutations: {
            // A mutation is an action the user took; repeating it silently is
            // worse than failing.
            retry: false,
          },
        },
      })
  );

  return <QueryClientProvider client={client}>{children}</QueryClientProvider>;
}
