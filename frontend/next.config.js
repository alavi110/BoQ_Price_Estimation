/**
 * The `/api/:path*` rewrite in `next.config.js` maps onto the backend's root.
 *
 * The backend mounts its routers at the root - `app.include_router(boq.router,
 * prefix="/boq")` and no global prefix - so the real paths are `/boq/upload` and
 * `/weights/{job_id}`, not `/api/boq/upload`. This rewrite therefore *drops* the
 * `/api` segment on the way out.
 *
 * That the dashboard works at all in development is a sign this was untested
 * rather than a sign the prefix was harmless: the client's own requests would 404
 * on every endpoint, and the JSON endpoints behind the rewrite have no client of
 * their own, so nothing noticed.
 */
const BACKEND_URL = process.env.NEXT_PUBLIC_API_URL || 'http://localhost:8000';

/** @type {import('next').NextConfig} */
const nextConfig = {
  reactStrictMode: true,
  output: 'standalone',
  images: {
    domains: ['localhost'],
  },
  async rewrites() {
    return [
      {
        // The path is forwarded verbatim, minus the `/api` this app owns. The
        // leading slash in the capture is what makes it relative: a destination of
        // `${BACKEND_URL}/:path*` yields `http://localhost:8000/boq/upload`, and
        // without it the capture would swallow the host and produce
        // `http://localhost:8000http://localhost:8000/boq/upload`.
        source: '/api/:path*',
        destination: `${BACKEND_URL}/:path*`,
      },
    ];
  },
};

module.exports = nextConfig;
