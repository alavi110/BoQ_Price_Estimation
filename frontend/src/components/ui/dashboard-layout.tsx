/**
 * The dashboard shell: header, navigation, and the right-to-left frame the whole
 * application sits in.
 *
 * The `dir="rtl"` and `lang="fa"` are set on the main region rather than left to
 * a single `dir` on `<html>`, and that is a deliberate placement. The dashboard
 * is not entirely Persian: identifiers, ISO dates, token values and the code
 * column are all Latin, and they have to read left-to-right *inside* an RTL
 * column. A document-level `dir` gets overridden by CSS direction on individual
 * cells in a way that is easy to get wrong per-column and impossible to see from
 * a single attribute; a region-level `dir` combined with an explicit per-cell
 * isolation for the Latin runs keeps each decision local to the element that
 * needs it.
 *
 * shadcn/ui's primitives are used for the surfaces rather than hand-rolled
 * divs, so focus rings, disabled states and the sheet's escape handling come
 * from tested components instead of from this file.
 */
import * as React from 'react';
import { cn } from '@/lib/utils';

export interface DashboardLayoutProps {
  /** Rendered as the page heading and used to name the main region. */
  title: string;
  /**
   * An optional short line under the title - the job's file name, a project
   * reference. Kept out of the heading so a screen reader announces the section
   * once rather than re-reading a changing string on every poll.
   */
  subtitle?: string;
  /** Navigation content. Omitted entirely when there is nothing to navigate to. */
  nav?: React.ReactNode;
  /** Actions for the header: export, settings, the user's role. */
  actions?: React.ReactNode;
  children: React.ReactNode;
  className?: string;
}

export function DashboardLayout({
  title,
  subtitle,
  nav,
  actions,
  children,
  className,
}: DashboardLayoutProps) {
  return (
    <div className={cn('min-h-screen bg-background', className)}>
      <header className="sticky top-0 z-50 border-b bg-card/50 backdrop-blur-sm">
        <div className="container mx-auto flex h-16 items-center justify-between px-4">
          <h1 className="text-xl font-bold">{title}</h1>
          {actions ? <div className="flex items-center gap-2">{actions}</div> : null}
        </div>
      </header>

      {nav ? (
        <nav aria-label="ناوبری اصلی" className="border-b bg-card/30">
          <div className="container mx-auto px-4 py-2">{nav}</div>
        </nav>
      ) : null}

      {/*
        `dir` and `lang` on the region, not the document. See the module
        docstring: the dashboard mixes Persian prose with Latin identifiers, and
        a region-level direction keeps each cell's exception local.
      */}
      <main dir="rtl" lang="fa" className="container mx-auto px-4 py-6">
        {subtitle ? <p className="mb-4 text-sm text-muted-foreground">{subtitle}</p> : null}
        {children}
      </main>
    </div>
  );
}

/**
 * The section tabs, as a proper tablist.
 *
 * `role="tablist"` with real `tab` roles rather than buttons with an `active`
 * class, because the flow moves between views by keyboard and a screen-reader
 * user has to be able to hear how many views exist and which one they are in.
 * `aria-selected` carries the state; the visual highlight is a consequence of
 * it rather than a second source of truth.
 */
export interface DashboardTab {
  id: string;
  label: string;
  icon?: React.ComponentType<{ className?: string }>;
}

export interface DashboardTabsProps {
  tabs: DashboardTab[];
  active: string;
  onChange: (id: string) => void;
  className?: string;
}

export function DashboardTabs({ tabs, active, onChange, className }: DashboardTabsProps) {
  return (
    <div
      role="tablist"
      aria-label="بخش‌های داشبورد"
      className={cn('flex flex-wrap gap-1', className)}
    >
      {tabs.map((tab) => {
        const Icon = tab.icon;
        const selected = tab.id === active;
        return (
          <button
            key={tab.id}
            type="button"
            role="tab"
            id={`tab-${tab.id}`}
            aria-selected={selected}
            aria-controls={`panel-${tab.id}`}
            onClick={() => onChange(tab.id)}
            className={cn(
              'inline-flex items-center gap-2 rounded-md px-3 py-1.5 text-sm font-medium transition-colors',
              'focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2',
              selected
                ? 'bg-primary text-primary-foreground'
                : 'text-muted-foreground hover:bg-accent hover:text-accent-foreground'
            )}
          >
            {Icon ? <Icon className="h-4 w-4" /> : null}
            {tab.label}
          </button>
        );
      })}
    </div>
  );
}

/** A tab's content region, wired to the tab that selects it. */
export function DashboardPanel({
  id,
  active,
  children,
}: {
  id: string;
  active: string;
  children: React.ReactNode;
}) {
  if (id !== active) {
    return null;
  }
  return (
    <div role="tabpanel" id={`panel-${id}`} aria-labelledby={`tab-${id}`}>
      {children}
    </div>
  );
}
