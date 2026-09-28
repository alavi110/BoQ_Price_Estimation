/**
 * The export dialog: the updated BoQ as Excel, and the defense document as PDF.
 *
 * Everything about this dialog is shaped by the fact that what it produces
 * leaves the building. The Excel workbook is submitted with a tender; the
 * defense document is the artifact a price dispute is argued from. Three
 * consequences follow, and they are the reason this is a dialog with explicit
 * scope and an explicit failure state rather than a row of download buttons.
 *
 * **Failures are loud.** A refused export that the UI swallows is the worst
 * outcome available here: the operator sees a button that worked, believes they
 * have a workbook, and submits nothing. So a 422 is rendered with the server's
 * own message and no download link is shown at all, and a transport failure is
 * reported as one rather than as a status code.
 *
 * **The sheet selection is explicit.** Three sheets, three checkboxes, and the
 * user can see what they are about to get. The API's own defaults are all
 * `true`; the dialog starts with the forecast sheet *on* as well, because the
 * quickstart's Scenario 6 validation requires it. Hiding the choice would make a
 * silently incomplete workbook indistinguishable from a complete one.
 *
 * **The document's scope is stated.** The defense document is generated for a
 * whole project, and a partial one is not a smaller document - it is a document
 * that will misrepresent a tender if it is submitted. The dialog says so rather
 * than leaving it to be inferred from the button.
 */
'use client';

import * as React from 'react';
import { CheckCircle2, FileSpreadsheet, FileText, X } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { ApiError, NETWORK_ERROR_MESSAGE } from '@/lib/api/client';
import {
  buildExcelExportPayload,
  useExportDefenseDoc,
  useExportExcel,
  type ExportDefenseDocResponse,
  type ExportExcelResponse,
} from '@/lib/api/queries';

export interface ExportModalProps {
  /** The BoQ job to export, or `null` when nothing has been uploaded. */
  jobId: string | null;
  projectId: string | null;
  /**
   * The dialog's state on mount.
   *
   * The dialog owns its open/closed state from here on and reports changes
   * through `onOpenChange`. Seeded rather than continuously controlled because
   * both callers are open-then-close: the dashboard opens it from a button in
   * the header, and a user closes it from inside. A fully controlled dialog
   * would need the parent to own a piece of state that only the dialog acts on.
   */
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
  /** Hide the trigger button when the parent renders its own. */
  hideTrigger?: boolean;
  className?: string;
}

type Outcome = { kind: 'excel'; result: ExportExcelResponse } | {
  kind: 'defense-doc';
  result: ExportDefenseDocResponse;
};

/** A download link for a completed export. */
function DownloadResult({ outcome }: { outcome: Outcome }) {
  return (
    <div className="flex items-center gap-2 rounded-md border border-emerald-500/40 bg-emerald-500/10 p-3 text-sm">
      <CheckCircle2 className="h-4 w-4 shrink-0 text-emerald-700" aria-hidden="true" />
      <span className="flex-1 font-medium">{outcome.result.filename}</span>
      {/*
        `download` and `rel="noopener"` together: the attribute means the browser
        offers to save rather than navigate, and `noopener` keeps the export page
        from reaching back through `window.opener` - the download endpoint is a
        different origin from the dashboard in development.
      */}
      <a
        href={outcome.result.download_url}
        download={outcome.result.filename}
        rel="noopener noreferrer"
        className="rounded-md bg-primary px-3 py-1.5 text-primary-foreground"
      >
        دانلود
      </a>
    </div>
  );
}

export function ExportModal({
  jobId,
  projectId,
  open: initialOpen = false,
  onOpenChange,
  hideTrigger = false,
  className,
}: ExportModalProps) {
  const [open, setOpen] = React.useState(initialOpen);
  const [includeWeights, setIncludeWeights] = React.useState(true);
  const [includeForecasts, setIncludeForecasts] = React.useState(true);
  const [includeComparison, setIncludeComparison] = React.useState(true);
  const [outcome, setOutcome] = React.useState<Outcome | null>(null);
  const [error, setError] = React.useState<string | null>(null);

  const excel = useExportExcel();
  const defense = useExportDefenseDoc();

  const exportable = Boolean(jobId && projectId);
  const busy = excel.isPending || defense.isPending;

  function setOpenState(next: boolean) {
    setOpen(next);
    onOpenChange?.(next);
    if (!next) {
      // A stale download link outlives the dialog it was produced in, and the
      // URL is signed with an expiry. Clearing on close means reopening shows
      // the state the user is actually in, not a link that will 410.
      setOutcome(null);
      setError(null);
    }
  }

  async function handleExcel() {
    if (!jobId || !projectId) {
      return;
    }
    setError(null);
    setOutcome(null);
    try {
      const result = await excel.mutateAsync(
        buildExcelExportPayload(jobId, projectId, {
          includeWeights,
          includeForecasts,
          includeComparison,
        })
      );
      setOutcome({ kind: 'excel', result });
    } catch (cause) {
      setError(describe(cause));
    }
  }

  async function handleDefense() {
    if (!jobId || !projectId) {
      return;
    }
    setError(null);
    setOutcome(null);
    try {
      const result = await defense.mutateAsync({
        job_id: jobId,
        project_id: projectId,
        format: 'pdf',
      });
      setOutcome({ kind: 'defense-doc', result });
    } catch (cause) {
      setError(describe(cause));
    }
  }

  const message = excel.error ?? defense.error;
  const shown = error ?? (message ? describe(message) : null);

  return (
    <div className={className}>
      {hideTrigger ? null : (
        <Button type="button" variant="outline" onClick={() => setOpenState(true)}>
          <FileSpreadsheet className="ml-2 h-4 w-4" aria-hidden="true" />
          خروجی
        </Button>
      )}

      {open ? (
        <div
          role="dialog"
          aria-modal="true"
          aria-labelledby="export-modal-title"
          className="fixed inset-0 z-50 flex items-center justify-center bg-black/50 p-4"
        >
          <div className="w-full max-w-lg space-y-4 rounded-lg border bg-background p-6 shadow-lg">
            <div className="flex items-center justify-between">
              <h2 id="export-modal-title" className="text-lg font-semibold">
                خروجی گرفتن
              </h2>
              <Button
                type="button"
                variant="ghost"
                size="icon"
                aria-label="بستن"
                onClick={() => setOpenState(false)}
              >
                <X className="h-4 w-4" />
              </Button>
            </div>

            {exportable ? null : (
              <Alert tone="warning">
                ابتدا یک فایل BoQ بارگذاری کنید تا خروجی گرفتن ممکن شود.
              </Alert>
            )}

            <section className="space-y-2">
              <h3 className="text-sm font-medium">خروجی Excel</h3>
              <p className="text-xs text-muted-foreground">
                فایل به‌روزشده BoQ به همراه برگه‌های وزن‌دهی، مقایسه قیمت و سناریوهای پیش‌بینی.
              </p>
              <fieldset className="space-y-1" disabled={busy}>
                <legend className="sr-only">برگه‌های فایل خروجی</legend>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={includeWeights}
                    onChange={(event) => setIncludeWeights(event.target.checked)}
                  />
                  برگه وزن‌دهی
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={includeForecasts}
                    onChange={(event) => setIncludeForecasts(event.target.checked)}
                  />
                  برگه سناریوهای پیش‌بینی
                </label>
                <label className="flex items-center gap-2 text-sm">
                  <input
                    type="checkbox"
                    checked={includeComparison}
                    onChange={(event) => setIncludeComparison(event.target.checked)}
                  />
                  برگه مقایسه قیمت
                </label>
              </fieldset>
              <Button
                type="button"
                onClick={() => void handleExcel()}
                disabled={!exportable || busy}
              >
                <FileSpreadsheet className="ml-2 h-4 w-4" aria-hidden="true" />
                دریافت خروجی Excel
              </Button>
            </section>

            <section className="space-y-2 border-t pt-4">
              <h3 className="text-sm font-medium">سند دفاعیه (PDF)</h3>
              {/*
                The scope warning is not a disclaimer. A defense document
                generated for a subset of the BoQ is not a shorter document, it is
                one that argues for a tender it does not describe, so the dialog
                says which scope will be produced. A `status` and not an `alert`:
                the export is still available, so this informs rather than blocks.
              */}
              <Alert tone="warning">
                این سند برای کل پروژه تولید می‌شود و شامل همه فصل‌ها و ردیف‌هاست.
              </Alert>
              <Button
                type="button"
                variant="secondary"
                onClick={() => void handleDefense()}
                disabled={!exportable || busy}
              >
                <FileText className="ml-2 h-4 w-4" aria-hidden="true" />
                تولید سند دفاعیه
              </Button>
            </section>

            {busy ? (
              <p role="status" className="text-sm text-muted-foreground">
                در حال تولید فایل، لطفاً صبر کنید…
              </p>
            ) : null}

            {/*
              The failure region. It is separate from the success region and they
              are never both visible: a link shown next to an error would let an
              operator download a stale file and believe it was the one just
              requested.
            */}
            {shown && !busy ? <Alert tone="error">{shown}</Alert> : null}

            {outcome && !shown ? <DownloadResult outcome={outcome} /> : null}
          </div>
        </div>
      ) : null}
    </div>
  );
}

/**
 * Turn any thrown value into a message to show.
 *
 * An `ApiError` already carries the server's own text - for an export that is
 * usually "no rows found for export", which is precise and actionable. A
 * transport failure gets the Persian connectivity message, because the raw
 * `TypeError: Failed to fetch` tells an operator nothing about what to do next.
 */
function describe(cause: unknown): string {
  if (cause instanceof ApiError) {
    return cause.isNetworkFailure ? NETWORK_ERROR_MESSAGE : cause.message;
  }
  if (cause instanceof Error) {
    return cause.message;
  }
  return 'خطای ناشناخته در تولید خروجی';
}
