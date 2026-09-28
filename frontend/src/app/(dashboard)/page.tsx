/**
 * The dashboard route.
 *
 * This is a thin wrapper on purpose. The dashboard's logic lives in
 * `./dashboard.tsx` rather than here because a page file under the App Router is
 * the boundary Next.js owns - it re-renders on navigation, and mixing component
 * state into it means a re-render can reset an in-progress analysis. Keeping the
 * component in its own module means the tests can mount it without a router, and
 * the page stays the one line it should be.
 */
import { Dashboard } from './dashboard';

export default function DashboardPage() {
  return <Dashboard />;
}
