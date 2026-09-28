/**
 * Export tests (US7, T097).
 *
 * An export is the artifact that leaves the building: the updated BoQ that gets
 * submitted, the defence document that gets argued from. So the two things that
 * matter are that the right *content* was requested, and that a failure is
 * visible rather than silent.
 *
 * The second one is the failure this file is really about. A download that
 * silently produces an empty workbook, or an export button that reports success
 * for a request the server refused, produces a document that looks authoritative
 * and is wrong - and it produces it silently. Every test below that can fail
 * quietly is asserted to fail loudly.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, waitFor, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { ExportModal } from '@/components/ui/export-modal';
import { buildExcelExportPayload } from '@/lib/api/queries';
import { JOB_ID, PROJECT_ID } from '../fixtures/analysis';

function withQueryClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 }, mutations: { retry: false } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

const EXCEL_OK = {
  ok: true,
  status: 200,
  json: async () => ({
    download_url: '/exports/excel/download/abc123',
    filename: 'boq-updated.xlsx',
    expires_at: '2026-09-28T09:00:00Z',
  }),
};

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

/** Parse the body of the single fetch call, whichever endpoint it hit. */
function sentBody(fetchMock: ReturnType<typeof vi.fn>, call = 0) {
  const init = fetchMock.mock.calls[call][1] as RequestInit;
  return JSON.parse(init.body as string);
}

function sentUrl(fetchMock: ReturnType<typeof vi.fn>, call = 0) {
  return fetchMock.mock.calls[call][0] as string;
}

describe('buildExcelExportPayload', () => {
  it('carries the job and project the export is for', () => {
    const payload = buildExcelExportPayload(JOB_ID, PROJECT_ID, {});

    expect(payload).toMatchObject({ job_id: JOB_ID, project_id: PROJECT_ID });
  });

  it('includes the weights, forecasts and comparison sheets by default', () => {
    // The quickstart's Scenario 6 validation lists all three sheets as required,
    // so an export that omitted one by default would fail acceptance while
    // looking like it had worked.
    const payload = buildExcelExportPayload(JOB_ID, PROJECT_ID, {});

    expect(payload).toMatchObject({
      include_weights: true,
      include_forecasts: true,
      include_comparison: true,
    });
  });

  it('honours each sheet being switched off', () => {
    const payload = buildExcelExportPayload(JOB_ID, PROJECT_ID, {
      includeForecasts: false,
    });

    expect(payload.include_forecasts).toBe(false);
    expect(payload.include_weights).toBe(true);
  });

  it('sends only the fields the API declares, so an extra key is not a 422', () => {
    // ExportExcelRequest is a strict Pydantic model; an unknown field is a
    // validation error, and it would arrive as a confusing 422 at click time.
    const payload = buildExcelExportPayload(JOB_ID, PROJECT_ID, {});

    expect(Object.keys(payload).sort()).toEqual([
      'include_comparison',
      'include_forecasts',
      'include_weights',
      'job_id',
      'project_id',
    ]);
  });
});

describe('ExportModal', () => {
  async function openModal() {
    const trigger = screen.getByRole('button', { name: /خروجی/ });
    await userEvent.click(trigger);
    return screen.findByRole('dialog');
  }

  function Harness(props: Partial<React.ComponentProps<typeof ExportModal>> = {}) {
    return (
      <>
        <button onClick={() => {}}>خروجی</button>
        <ExportModal jobId={JOB_ID} projectId={PROJECT_ID} {...props} />
      </>
    );
  }

  it('is closed until the export button is pressed', () => {
    withQueryClient(<Harness />);

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
  });

  it('opens a dialog naming both output formats', async () => {
    withQueryClient(<Harness open />);

    const dialog = screen.getByRole('dialog');
    expect(within(dialog).getByText('خروجی Excel')).toBeInTheDocument();
    expect(within(dialog).getByText('سند دفاعیه (PDF)')).toBeInTheDocument();
  });

  it('sends exactly the sheets the user selected', async () => {
    const fetchMock = vi.fn().mockResolvedValue(EXCEL_OK);
    vi.stubGlobal('fetch', fetchMock);

    withQueryClient(<Harness open />);
    const dialog = screen.getByRole('dialog');

    // Every sheet starts included, matching the API's own defaults and
    // Scenario 6's required output.
    await userEvent.click(within(dialog).getByRole('checkbox', { name: /پیش‌بینی/ }));
    await userEvent.click(
      within(dialog).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sentUrl(fetchMock)).toContain('/exports/excel');
    expect(sentBody(fetchMock)).toMatchObject({
      job_id: JOB_ID,
      project_id: PROJECT_ID,
      include_forecasts: false,
      // Switching one sheet off must not switch the others off with it.
      include_weights: true,
      include_comparison: true,
    });
  });

  it('shows the download link and filename once the export is ready', async () => {
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(EXCEL_OK));

    withQueryClient(<Harness open />);
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    expect(await screen.findByText('boq-updated.xlsx')).toBeInTheDocument();
    const link = screen.getByRole('link', { name: /دانلود/ });
    expect(link).toHaveAttribute('href', '/exports/excel/download/abc123');
  });

  it('reports a server refusal instead of presenting an empty success', async () => {
    // The failure this file exists for: a 422 that the UI swallows looks
    // exactly like a successful export, and the operator then submits a file
    // that was never generated.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: 'هیچ ردیفی برای خروجی یافت نشد' }),
      })
    );

    withQueryClient(<Harness open />);
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('هیچ ردیفی برای خروجی یافت نشد');
    expect(screen.queryByRole('link', { name: /دانلود/ })).not.toBeInTheDocument();
  });

  it('reports a network failure in Persian, without leaking the transport error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    withQueryClient(<Harness open />);
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/ارتباط با سرور/);
    expect(alert).not.toHaveTextContent('Failed to fetch');
  });

  it('does not offer the export at all without a job to export', () => {
    // An export button enabled with no job id produces a request the server
    // cannot answer; the honest state is to say the export is not available.
    withQueryClient(
      <ExportModal jobId={null} projectId={null} open />
    );

    expect(
      screen.getByRole('button', { name: /دریافت خروجی Excel/ })
    ).toBeDisabled();
    expect(screen.getByText(/ابتدا یک فایل BoQ/)).toBeInTheDocument();
  });

  it('lets the user close the dialog without exporting', async () => {
    const fetchMock = vi.fn();
    vi.stubGlobal('fetch', fetchMock);

    withQueryClient(<Harness open />);
    const dialog = screen.getByRole('dialog');
    await userEvent.click(within(dialog).getByRole('button', { name: /بستن/ }));

    expect(screen.queryByRole('dialog')).not.toBeInTheDocument();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it('disables the button while the export is in flight, so it cannot be double-submitted', async () => {
    let release: (value: unknown) => void = () => {};
    vi.stubGlobal(
      'fetch',
      vi.fn().mockImplementation(
        () => new Promise((resolve) => (release = resolve as (v: unknown) => void))
      )
    );

    withQueryClient(<Harness open />);
    const button = screen.getByRole('button', { name: /دریافت خروجی Excel/ });
    await userEvent.click(button);

    await waitFor(() => expect(button).toBeDisabled());
    // Named by content rather than by role: the dialog also carries a standing
    // `status` region for the defence document's project-wide scope, so
    // `getByRole('status')` alone would match two things and this assertion
    // would pass on the wrong one.
    expect(screen.getByText(/در حال تولید فایل/)).toBeInTheDocument();

    release(EXCEL_OK);
  });

  it('requests the defence document as PDF', async () => {
    const fetchMock = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({
        download_url: '/exports/defense-doc/download/xyz789',
        filename: 'defense-doc.pdf',
        expires_at: '2026-09-28T09:00:00Z',
      }),
    });
    vi.stubGlobal('fetch', fetchMock);

    withQueryClient(<Harness open />);
    await userEvent.click(
      within(screen.getByRole('dialog')).getByRole('button', { name: /تولید سند دفاعیه/ })
    );

    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1));
    expect(sentUrl(fetchMock)).toContain('/exports/defense-doc');
    expect(sentBody(fetchMock)).toMatchObject({ format: 'pdf' });
  });

  it('warns that the defence document is generated for a whole project', async () => {
    // The document is the artifact a dispute is argued from. Generating a
    // partial one by accident is not a recoverable mistake, so the scope is
    // stated rather than left to be inferred.
    withQueryClient(<Harness open />);

    expect(screen.getByText(/کل پروژه/)).toBeInTheDocument();
  });
});
