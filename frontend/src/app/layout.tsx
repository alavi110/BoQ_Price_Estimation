import type { Metadata } from 'next';
import './globals.css';

import { Providers } from '@/components/providers';

export const metadata: Metadata = {
  title: 'BoQ Price Forecast',
  description: 'AI-powered BoQ price adjustment and forecasting system',
};

export default function RootLayout({
  children,
}: {
  children: React.ReactNode;
}) {
  return (
    /*
     * `lang` and `dir` on the `<html>` element, not on a wrapper inside the body.
     *
     * This is not a styling preference. `dir="rtl"` set deeper in the tree leaves
     * the first screenful - the part the browser paints before hydration - laid
     * out left-to-right, so a Persian reader sees the numbers in reverse order
     * and then sees them correct a moment later. A screen reader also picks
     * `lang` up from the nearest ancestor of the text, and picks it up from
     * `<html>` only if it is there.
     */
    <html lang="fa" dir="rtl">
      <body>
        <Providers>{children}</Providers>
      </body>
    </html>
  );
}
