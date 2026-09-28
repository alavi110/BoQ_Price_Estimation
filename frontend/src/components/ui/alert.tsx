/**
 * The failure and warning banner, in one component.
 *
 * It is a component rather than a repeated `<div className="...">` because three
 * things about it are easy to get subtly wrong at each call site, and every one
 * of them is a message that fails to reach the person who can act on it.
 *
 * The `role` is not decoration. `alert` is announced immediately and
 * interruptively, which is right for a failure the user is waiting on and wrong
 * for anything routine. `status` is the polite variant and is used for progress
 * and for disclosures - a missing-price-history notice is information the reader
 * needs before they trust a number, not an interruption. Getting these the wrong
 * way round means either a screen reader interrupts on every render, or an upload
 * failure is never announced at all.
 *
 * The `tone` is likewise semantic. An error is something the user must resolve
 * before proceeding; a warning is something they should know about. A warning
 * painted in the error colour trains people to ignore error banners, and an
 * error painted in the warning colour gets filed under "not important".
 */
import * as React from 'react';
import { AlertTriangle, Info } from 'lucide-react';
import { cn } from '@/lib/utils';

export type AlertTone = 'error' | 'warning' | 'info';

const TONE_CLASSES: Record<AlertTone, string> = {
  error: 'border-red-500/40 bg-red-500/10 text-red-800',
  warning: 'border-amber-500/40 bg-amber-500/10 text-amber-900',
  info: 'border-sky-500/40 bg-sky-500/10 text-sky-900',
};

const TONE_ROLES: Record<AlertTone, 'alert' | 'status'> = {
  // A failure interrupts. A disclosure or a progress note waits its turn.
  error: 'alert',
  warning: 'status',
  info: 'status',
};

export interface AlertProps extends React.HTMLAttributes<HTMLDivElement> {
  tone?: AlertTone;
  /** Overrides the role implied by the tone. Use only with a specific reason. */
  role?: 'alert' | 'status';
  children: React.ReactNode;
}

export function Alert({ tone = 'error', role, className, children, ...props }: AlertProps) {
  const resolvedRole = role ?? TONE_ROLES[tone];
  const Icon = tone === 'info' ? Info : AlertTriangle;

  return (
    <div
      // `aria-live` is redundant for `alert` (implicit) and necessary for
      // `status`, where a message that arrives after a re-render would otherwise
      // be missed by a reader that was not focused on the region.
      aria-live={resolvedRole === 'status' ? 'polite' : undefined}
      role={resolvedRole}
      className={cn(
        'flex items-start gap-2 rounded-md border p-3 text-sm',
        TONE_CLASSES[tone],
        className
      )}
      {...props}
    >
      <Icon className="mt-0.5 h-4 w-4 shrink-0" aria-hidden="true" />
      <div className="flex-1">{children}</div>
    </div>
  );
}
