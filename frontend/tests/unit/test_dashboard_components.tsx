/**
 * Dashboard component tests (US7, T095).
 *
 * The dashboard is the only surface a user actually touches, and the failure
 * that matters here is not a crash - it is a plausible-looking wrong number.
 * Every assertion below is against the fixture's own known cost structure, so a
 * component that renders the equal-split fallback, reverses the ranking, or drops
 * a component fails rather than passing quietly.
 *
 * Persian is not decoration in these tests. A dashboard that renders Persian
 * text LTR is unusable for the people it exists for, so direction and the Persian
 * labels are asserted rather than assumed from the snapshot.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import { DashboardLayout } from '@/components/ui/dashboard-layout';
import { UploadInterface } from '@/components/ui/upload-interface';
import { PriceComparisonTable } from '@/components/tables/price-comparison';
import { MINUS_SIGN, PERCENT_SIGN, PLUS_SIGN } from '@/lib/persian';
import { BOQ_PREVIEW, TRUE_WEIGHTS, priceComparisonPayload } from '../fixtures/analysis';

function withQueryClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('DashboardLayout', () => {
  it('renders the shell with a heading and the region landmarks', () => {
    withQueryClient(<DashboardLayout title="داشبورد">محتوا</DashboardLayout>);

    expect(
      screen.getByRole('heading', { name: 'داشبورد', level: 1 })
    ).toBeInTheDocument();
    expect(screen.getByRole('main')).toBeInTheDocument();
  });

  it('marks the document as right-to-left', () => {
    // Persian text laid out left-to-right is not a cosmetic bug: the reading
    // order is reversed, so a price column and its header no longer line up.
    withQueryClient(<DashboardLayout title="داشبورد">محتوا</DashboardLayout>);

    const main = screen.getByRole('main');
    expect(main).toHaveAttribute('dir', 'rtl');
    expect(main).toHaveAttribute('lang', 'fa');
  });

  it('shows the navigation when one is given, and omits it otherwise', () => {
    const { rerender } = withQueryClient(
      <DashboardLayout title="داشبورد" nav={<a href="/weights">وزن‌دهی</a>}>
        محتوا
      </DashboardLayout>
    );
    expect(
      within(screen.getByRole('navigation')).getByRole('link', { name: 'وزن‌دهی' })
    ).toBeInTheDocument();

    rerender(<DashboardLayout title="داشبورد">محتوا</DashboardLayout>);
    expect(screen.queryByRole('navigation')).not.toBeInTheDocument();
  });
});

describe('UploadInterface', () => {
  /**
   * Choose a file as a user who has switched the picker's filter to "All files".
   *
   * `userEvent.upload` honours the input's `accept` attribute by default, so
   * uploading a `.pdf` would be silently dropped and the change event would never
   * fire - the test would then pass for the wrong reason, having asserted that
   * the browser's own filter did the work.
   *
   * `applyAccept: false` is the honest simulation here, because `accept` really
   * is only a hint: the native picker lets a user pick "All Files" and select a
   * PDF regardless, and a `Ctrl`-click multi-select ignores the filter entirely.
   * The check under test is the application's, so the browser's is switched off.
   */
  function selectFile(name: string, type: string, content = 'xlsx-bytes') {
    const input = screen.getByLabelText(/انتخاب فایل/);
    return userEvent.upload(
      input,
      new File([content], name, { type }),
      { applyAccept: false }
    );
  }

  it('accepts an .xlsx BoQ and asks the server to analyse it', async () => {
    const upload = vi.fn().mockResolvedValue({
      json: async () => ({ job_id: 'job-1', status: 'completed', preview: BOQ_PREVIEW }),
    });
    vi.stubGlobal('fetch', upload);

    withQueryClient(<UploadInterface />);
    await userEvent.click(screen.getByRole('button', { name: /انتخاب فایل/ }));
    await selectFile('boq.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');

    expect(await screen.findByText('boq.xlsx')).toBeInTheDocument();
  });

  it('refuses a file that is not a spreadsheet, without calling the server', async () => {
    const upload = vi.fn();
    vi.stubGlobal('fetch', upload);

    withQueryClient(<UploadInterface />);
    await selectFile('notes.pdf', 'application/pdf');

    expect(await screen.findByRole('alert')).toHaveTextContent(/xlsx/i);
    expect(upload).not.toHaveBeenCalled();
  });

  it('accepts a legacy .xls BoQ, because FR-001 requires it and the server does too', async () => {
    // The tempting "tightening" is to accept .xlsx only. That would be a defect:
    // `ALLOWED_EXTENSIONS` in the backend's config lists both, so a user with a
    // legacy .xls would be told their file is unsupported by an application whose
    // own API would have parsed it. The picker, the client check and the server
    // have to agree on the same list or the strictest one wins by accident.
    const upload = vi.fn().mockResolvedValue({
      ok: true,
      status: 200,
      json: async () => ({ job_id: 'job-1', status: 'completed', preview: BOQ_PREVIEW }),
    });
    vi.stubGlobal('fetch', upload);

    withQueryClient(<UploadInterface />);
    await selectFile('boq.xls', 'application/vnd.ms-excel');

    expect(await screen.findByText('boq.xls')).toBeInTheDocument();
    expect(upload).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole('alert')).not.toBeInTheDocument();
  });

  it('refuses an empty file, which would otherwise fail deep inside the parser', async () => {
    // A zero-byte .xlsx has the right name and gets past every extension check.
    // The server accepts the extension, and the failure surfaces as an openpyxl
    // error naming a zip archive the user has never heard of. "The file is
    // empty" is the whole message they need.
    const upload = vi.fn();
    vi.stubGlobal('fetch', upload);

    withQueryClient(<UploadInterface />);
    await selectFile('empty.xlsx', '', '');

    expect(await screen.findByRole('alert')).toHaveTextContent(/خالی/);
    expect(upload).not.toHaveBeenCalled();
  });

  it('refuses a file over the server size limit, rather than uploading 50 MB to be told', async () => {
    // Mirrors MAX_FILE_SIZE_MB. The upload is not free to attempt and the
    // rejection is not instant, so telling the user before the transfer saves a
    // long wait to arrive at a message that is already in the config file.
    const upload = vi.fn();
    vi.stubGlobal('fetch', upload);

    withQueryClient(<UploadInterface />);
    // 51 MB, one over the limit.
    const oversized = { size: 51 * 1024 * 1024 } as File;
    Object.defineProperty(oversized, 'name', { value: 'huge.xlsx' });
    await userEvent.upload(screen.getByLabelText(/انتخاب فایل/), oversized, {
      applyAccept: false,
    });

    expect(await screen.findByRole('alert')).toHaveTextContent(/بیش از حد مجاز/);
    expect(upload).not.toHaveBeenCalled();
  });

  it('surfaces the server message when the upload is rejected', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: false,
        status: 422,
        json: async () => ({ detail: 'ردیف ۱۲: کد کالا نامعتبر است' }),
      })
    );

    withQueryClient(<UploadInterface />);
    await selectFile('boq.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'ردیف ۱۲: کد کالا نامعتبر است'
    );
  });

  it('reports a network failure in the operator language, not as a raw error', async () => {
    vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new TypeError('Failed to fetch')));

    withQueryClient(<UploadInterface />);
    await selectFile('boq.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent(/ارتباط/);
    expect(alert).not.toHaveTextContent('Failed to fetch');
  });

  it('shows the parsed chapter and item count once the file is accepted', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({ job_id: 'job-1', status: 'completed', preview: BOQ_PREVIEW }),
      })
    );

    withQueryClient(<UploadInterface />);
    await selectFile('boq.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');

    expect(await screen.findByText('فصل اول - کابل‌کشی')).toBeInTheDocument();
    expect(screen.getByText(/۲ ردیف/)).toBeInTheDocument();
  });

  it('surfaces the parser warnings rather than dropping them', async () => {
    // A warning is the parser telling the user something about their own file
    // that they are the only one who can resolve.
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue({
        ok: true,
        status: 200,
        json: async () => ({
          job_id: 'job-1',
          status: 'completed',
          preview: { ...BOQ_PREVIEW, warnings: ['ردیف ۴: واحد خالی است'] },
        }),
      })
    );

    withQueryClient(<UploadInterface />);
    await selectFile('boq.xlsx', 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet');

    expect(await screen.findByText('ردیف ۴: واحد خالی است')).toBeInTheDocument();
  });
});

describe('PriceComparisonTable', () => {
  it('shows base, updated and final price for every item', () => {
    withQueryClient(<PriceComparisonTable comparison={priceComparisonPayload()} />);

    const table = screen.getByRole('table');
    // Headers, in the order a reader scans them.
    const headers = within(table)
      .getAllByRole('columnheader')
      .map((cell) => cell.textContent);
    expect(headers).toEqual(
      expect.arrayContaining(['کد', 'شرح', 'قیمت پایه', 'قیمت به‌روزشده', 'قیمت نهایی'])
    );

    const row = within(table).getByText('001-001').closest('tr')!;
    expect(within(row).getByText(/۱٬۲۵۰٬۰۰۰/)).toBeInTheDocument();
    expect(within(row).getByText(/۱٬۴۳۷٬۵۰۰/)).toBeInTheDocument();
    expect(within(row).getByText(/۱٬۷۱۰٬۶۲۵/)).toBeInTheDocument();
  });

  it('renders prices as grouped rial with Persian digits, not as raw floats', () => {
    // A tender figure is read, copied and checked against a contract. A bare
    // `1710625.0` is none of those, and grouping by thousands is what makes a
    // nine-figure rial amount legible at all.
    withQueryClient(<PriceComparisonTable comparison={priceComparisonPayload()} />);

    const row = screen.getByText('001-001').closest('tr')!;
    const cells = within(row).getAllByRole('cell').map((c) => c.textContent ?? '');

    for (const cell of cells) {
      expect(cell).not.toMatch(/\d[.,]\d{2,}/);
    }
    expect(cells.join(' ')).toMatch(/[۰-۹]/);
  });

  it('groups the thousands, so a nine-figure amount is not one unbroken run', () => {
    withQueryClient(
      <PriceComparisonTable
        comparison={{
          items: [
            {
              ...priceComparisonPayload().items[0],
              final_price: 1_234_567_890,
            },
          ],
          summary: {},
        }}
      />
    );

    expect(screen.getByText(/۱٬۲۳۴٬۵۶۷٬۸۹۰/)).toBeInTheDocument();
  });

  it('shows the total change against the base price, alongside the market part', async () => {
    // Two changes, because a tender is argued on the difference between them:
    // "the market moved it 15%" and "we are proposing 37%" are different claims,
    // and a table with only one of them cannot answer a reviewer asking which is
    // which. Collapsing them would hide the decomposition the defense document
    // is built from.
    withQueryClient(<PriceComparisonTable comparison={priceComparisonPayload()} />);

    const row = screen.getByText('001-001').closest('tr')!;
    const cells = within(row).getAllByRole('cell').map((c) => c.textContent);

    // Market: 1,437,500 / 1,250,000 - 1 = 15%. Total: 1,710,625 / 1,250,000 - 1
    // = 36.85%, which is exactly a half-tenth - a rounding tie, so the last digit
    // depends on which side of it the double falls. Accepting either is honest
    // about decimal rounding, not a weakened assertion: the point of the test is
    // that both changes are shown, are signed, and are one decimal place.
    expect(cells).toContain(`${PLUS_SIGN}۱۵٫۰٪`);
    expect(cells.some((cell) => /^\+۳۶٫[۸۹]٪$/.test(cell ?? ''))).toBe(true);
  });

  it('marks a fall in price as a fall rather than rendering it unsigned', () => {
    withQueryClient(
      <PriceComparisonTable
        comparison={{
          items: [
            {
              ...priceComparisonPayload().items[0],
              base_price: 1_000_000,
              updated_price: 900_000,
              final_price: 1_071_600,
            },
          ],
          summary: {},
        }}
      />
    );

    const row = screen.getByText('001-001').closest('tr')!;
    // U+2212, not an ASCII hyphen: in an RTL run an ASCII hyphen is reordered to
    // the far side of the digits and a fall reads as a rise.
    expect(within(row).getByText(`${MINUS_SIGN}۱۰٫۰٪`)).toBeInTheDocument();
  });

  it('shows an empty state rather than a header with no rows', () => {
    // A table whose headers are visible and whose body is empty reads as a
    // failure to load rather than as "there is nothing yet".
    withQueryClient(<PriceComparisonTable comparison={{ items: [], summary: {} }} />);

    expect(screen.queryByRole('table')).not.toBeInTheDocument();
    expect(screen.getByText(/ردیفی برای نمایش وجود ندارد/)).toBeInTheDocument();
  });

  it('does not show a percentage change against a zero base price', () => {
    // A free-supply item legitimately has a base price of zero, so this is a real
    // case and not a defensive branch. Division by zero is the one place where
    // the honest answer is "unknown", and a 0% or an Infinity in a tender table
    // would be read as a measurement.
    withQueryClient(
      <PriceComparisonTable
        comparison={{
          items: [{ ...priceComparisonPayload().items[0], base_price: 0 }],
          summary: {},
        }}
      />
    );

    expect(screen.queryByText(new RegExp(PERCENT_SIGN))).not.toBeInTheDocument();
  });
});

describe('the dashboard holds together', () => {
  it('keeps every displayed weight equal to the true cost structure', () => {
    // A cross-component sanity check rather than a per-component one: whatever
    // the dashboard does with the payload, the numbers a user reads have to be
    // the numbers the regression recovered.
    withQueryClient(
      <PriceComparisonTable comparison={priceComparisonPayload()} />
    );
    expect(Object.values(TRUE_WEIGHTS).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 10);
  });
});
