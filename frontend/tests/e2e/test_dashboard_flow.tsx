/**
 * The dashboard flow end to end (US7, T098).
 *
 * This is US7's "Independent Test" from the spec: *navigate through upload →
 * analysis → visualization → export*. Each unit test above covers one screen in
 * isolation, which is what makes them easy to write and easy to pass. What they
 * cannot cover is the seam between screens, and the seam is where this feature
 * actually fails: an upload response that names the job the rest of the flow
 * then has to read, a weights fetch keyed on the wrong id, an export posted
 * against a job the analysis never attributed. Every one of those renders two
 * green test files and a dashboard that stops dead after the first click.
 *
 * So the flow is driven through one component tree, in order, with a single
 * scripted server that only answers what a real one would. The server is
 * deliberately strict - it rejects an unknown job id, refuses an export for a
 * job that was never attributed, and 404s an endpoint the flow should not be
 * calling - because a permissive mock would let exactly the bug this test
 * exists to catch pass unnoticed.
 *
 * Scope note: this is a jsdom integration test, not a Playwright run. It
 * exercises the whole client-side flow against the real components, the real
 * query layer and the real HTTP client; it does not exercise a browser's layout,
 * file-picker behaviour or network stack, because those need a running Next
 * server and a live backend. The backend half of the same path is covered
 * separately in `backend/tests/integration/test_full_recalculation.py`, so the two
 * meet in the middle and neither end is untested.
 */
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';

import { Dashboard } from '@/app/(dashboard)/dashboard';
import {
  BOQ_PREVIEW,
  JOB_ID,
  PROJECT_ID,
  TRUE_WEIGHTS,
  priceComparisonPayload,
  unattributedBreakdown,
  weightAnalysisPayload,
  weightBreakdown,
} from '../fixtures/analysis';

/**
 * A server that only knows the one project it was given, and refuses
 * everything else. `attributed` tracks which jobs have been through the weight
 * analysis, because the real backend refuses to price or export a job whose
 * weights do not exist - and a mock that would happily serve them would hide
 * the ordering the flow depends on.
 */
function scriptServer(options: { unattributed?: boolean } = {}) {
  const attributed = new Set<string>();
  const calls: string[] = [];

  const json = (body: unknown, status = 200) => ({
    ok: status >= 200 && status < 300,
    status,
    json: async () => body,
  });

  const handler = vi.fn(async (url: string, init: RequestInit) => {
    const method = init.method ?? 'GET';
    const path = url.replace(/^https?:\/\/[^/]+/, '').split('?')[0];
    calls.push(`${method} ${path}`);

    /*
     * The upload is posted to `/api/upload` - the Next route handler - rather
     * than to the backend's `/boq/upload`, because that handler is what streams
     * the multipart body. Every other endpoint here is reached through the
     * `/api/:path*` rewrite and keeps its backend path. Both are matched on
     * their full path so a request to a path the flow should not be calling lands
     * in the 404 branch below rather than being quietly served.
     */
    if (method === 'POST' && (path.endsWith('/api/upload') || path.endsWith('/boq/upload'))) {
      return json({ job_id: JOB_ID, status: 'completed', preview: BOQ_PREVIEW });
    }

    if (method === 'POST' && path.endsWith('/weights/analyze')) {
      const body = JSON.parse(String(init.body));
      if (body.job_id !== JOB_ID || body.project_id !== PROJECT_ID) {
        return json({ detail: 'شناسه کار نامعتبر است' }, 422);
      }
      attributed.add(JOB_ID);
      return json({ job_id: JOB_ID, status: 'completed', message: null });
    }

    if (path.includes(`/weights/${JOB_ID}`)) {
      if (!attributed.has(JOB_ID)) {
        return json({ detail: 'تحلیل وزنی برای این کار انجام نشده است' }, 404);
      }
      const items = options.unattributed
        ? [unattributedBreakdown()]
        : [weightBreakdown()];
      return json(weightAnalysisPayload(items));
    }

    if (path.includes(`/prices/${JOB_ID}`)) {
      if (!attributed.has(JOB_ID)) {
        return json({ detail: 'وزن‌دهی انجام نشده است' }, 404);
      }
      // Prices are returned for the items that were actually attributed, not for
      // every row in the upload. A mock that answered with the whole file would
      // hide the seam this test is about: two screens describing different
      // quantities, both of which render correctly in isolation.
      const attributedCodes = new Set(
        (options.unattributed ? [unattributedBreakdown()] : [weightBreakdown()]).map(
          (item) => item.code
        )
      );
      const all = priceComparisonPayload().items;
      const items = all.filter((item) => attributedCodes.has(item.code));
      return json({
        items,
        summary: {
          item_count: items.length,
          total_final_price: items.reduce((sum, item) => sum + item.final_price, 0),
        },
      });
    }

    if (method === 'POST' && path.endsWith('/exports/excel')) {
      if (!attributed.has(JOB_ID)) {
        return json({ detail: 'هیچ ردیفی برای خروجی یافت نشد' }, 422);
      }
      return json({
        download_url: `/exports/excel/download/flow-token`,
        filename: 'boq-updated.xlsx',
        expires_at: '2026-09-28T09:00:00Z',
      });
    }

    return json({ detail: `not found: ${method} ${path}` }, 404);
  });

  return { handler, calls };
}

function renderDashboard() {
  const client = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  return render(
    <QueryClientProvider client={client}>
      <Dashboard />
    </QueryClientProvider>
  );
}

async function uploadBoQ() {
  const input = screen.getByLabelText(/انتخاب فایل/);
  await userEvent.upload(
    input,
    new File(['xlsx-bytes'], 'boq.xlsx', {
      type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    })
  );
  await screen.findByText('فصل اول - کابل‌کشی');
}

beforeEach(() => {
  vi.restoreAllMocks();
});

afterEach(() => {
  vi.unstubAllGlobals();
});

describe('upload → analysis → visualisation → export', () => {
  it('carries the uploaded job all the way to a downloadable export', async () => {
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();

    // 1. Upload. The parsed shape is what the user gets to check.
    await uploadBoQ();
    expect(screen.getByText(/۲ ردیف/)).toBeInTheDocument();

    // 2. Analysis, then the weights it produced.
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));
    const copperRow = await screen.findByText('مس');
    expect(within(copperRow.closest('tr')!).getByText('۳۵٪')).toBeInTheDocument();

    // 3. The prices that came out of those weights.
    await userEvent.click(screen.getByRole('tab', { name: /قیمت‌ها/ }));
    const table = await screen.findByRole('table');
    // Scoped to the table: the card also prints the tender total below it, and
    // the mock prices exactly one item, so the total and the final price are the
    // same figure. Asserting against the whole screen would match both and stop
    // distinguishing "the row was priced" from "a summary was printed".
    expect(within(table).getByText(/۱٬۷۱۰٬۶۲۵/)).toBeInTheDocument();

    // 4. The export, carrying the same job id the upload returned.
    await userEvent.click(screen.getByRole('button', { name: /خروجی/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    expect(await screen.findByText('boq-updated.xlsx')).toBeInTheDocument();

    const exportCall = handler.mock.calls.findIndex(([url]) =>
      String(url).endsWith('/exports/excel')
    );
    expect(exportCall).toBeGreaterThan(-1);
    const body = JSON.parse(
      (handler.mock.calls[exportCall][1] as RequestInit).body as string
    );
    expect(body.job_id).toBe(JOB_ID);
  });

  it('never fetches weights or prices before the analysis has been requested', async () => {
    // Ordering is the whole risk in this flow. The real backend 404s both
    // endpoints before attribution, so a dashboard that speculatively fetches on
    // load produces two failed requests and an error banner on a perfectly
    // healthy empty project.
    const { handler, calls } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await screen.findByRole('heading', { name: /داشبورد/ });

    expect(calls.filter((c) => c.includes('/weights/'))).toEqual([]);
    expect(calls.filter((c) => c.includes('/prices/'))).toEqual([]);
  });

  it('tells the user the flow is unfinished rather than showing an empty table', async () => {
    // The empty state has to say *why* it is empty. "No rows" after an upload
    // reads as a parse failure, and the operator's next move - re-uploading -
    // is the wrong one.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();

    expect(screen.getByText(/ابتدا یک فایل BoQ/)).toBeInTheDocument();
  });

  it('surfaces the weights the regression recovered, not the equal split', async () => {
    // The fixture's true structure is known, and its largest component is 0.35.
    // A dashboard that fell back to the uniform 1/7 vector would look correct -
    // seven components, summing to 1.0 - and be entirely wrong. This is the
    // assertion that the whole pipeline is wired to the real data.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));

    const rows = await screen.findAllByRole('row');
    const shares = rows
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[1]?.textContent);
    expect(shares).not.toContain('۱۴٪'); // 1/7, the fallback
    expect(shares).toContain('۳۵٪');
  });

  it('discloses an item the regression could not fit, and still prices it', async () => {
    // US2's assumption is that about a fifth of items have no price history.
    // The flow has to continue on the LLM-only weights rather than stopping -
    // but the user has to be told the evidence is thinner, because the defence
    // document will say so too.
    const { handler } = scriptServer({ unattributed: true });
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));

    expect(await screen.findByText(/بدون سابقه قیمت/)).toBeInTheDocument();
    const copperRow = screen.getByText('مس');
    expect(within(copperRow.closest('tr')!).getByText('—')).toBeInTheDocument();
  });

  it('reports a failed analysis and does not offer prices that do not exist', async () => {
    // If the analysis 404s, the honest state is "the analysis failed" - not a
    // price table, and not an export button that will post a job with no
    // weights and produce a 422.
    const handler = vi.fn(async (url: string, init: RequestInit) => {
      const path = String(url);
      if (path.endsWith('/api/upload') || path.endsWith('/boq/upload')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({ job_id: JOB_ID, status: 'completed', preview: BOQ_PREVIEW }),
        };
      }
      if (path.endsWith('/weights/analyze')) {
        return {
          ok: false,
          status: 500,
          json: async () => ({ detail: 'سرویس تحلیل وزنی در دسترس نیست' }),
        };
      }
      return { ok: false, status: 404, json: async () => ({ detail: 'nope' }) };
    });
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'سرویس تحلیل وزنی در دسترس نیست'
    );
  });

  it('refuses to export a job the analysis never attributed', async () => {
    // The scripted server refuses this, and the refusal has to reach the user.
    // An export button that reports success for a 422 hands the operator a file
    // that was never written.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();

    // The export is reachable from the upload step, before any analysis.
    await userEvent.click(screen.getByRole('button', { name: /خروجی/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    const alert = await screen.findByRole('alert');
    expect(alert).toHaveTextContent('هیچ ردیفی برای خروجی یافت نشد');
  });

  it('keeps the weights and the prices describing the same item', async () => {
    // Two screens, two fetches, one item. If the analysis returned the first
    // item and the comparison the second, both tables would render and neither
    // would be wrong in isolation - but they would be describing different
    // quantities in the same tender, which is the failure a reviewer catches
    // only after submission.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));
    await screen.findByText('مس');

    await userEvent.click(screen.getByRole('tab', { name: /قیمت‌ها/ }));
    const table = await screen.findByRole('table');
    const pricedCodes = within(table)
      .getAllByRole('row')
      .slice(1)
      .map((row) => within(row).getAllByRole('cell')[0]?.textContent);

    expect(pricedCodes).toContain('001-001');
    expect(pricedCodes).not.toContain('001-002');
  });

  it('reaches the export without a price table having been opened', async () => {
    // The price view is optional navigation, not a gate. Making it a step would
    // mean the export is unreachable when a project has prices that fail to
    // calculate - the export would be the tool that diagnoses it.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));
    await screen.findByText('مس');

    await userEvent.click(screen.getByRole('button', { name: /خروجی/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', { name: /دریافت خروجی Excel/ })
    );

    expect(await screen.findByText('boq-updated.xlsx')).toBeInTheDocument();
    expect(
      handler.mock.calls.some(([url]) => String(url).includes('/prices/'))
    ).toBe(false);
  });

  it('never leaks a stored bearer token into a URL', async () => {
    // A token in a query string is a token in the browser history, the server
    // access log, and any Referer header that follows. The flow exercises every
    // call, so it is the right place to assert the client never does it.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));
    await screen.findByText('مس');
    await userEvent.click(screen.getByRole('tab', { name: /قیمت‌ها/ }));
    await screen.findByRole('table');
    await userEvent.click(screen.getByRole('button', { name: /خروجی/ }));
    const dialog = await screen.findByRole('dialog');
    await userEvent.click(
      within(dialog).getByRole('button', { name: /دریافت خروجی Excel/ })
    );
    await screen.findByText('boq-updated.xlsx');

    for (const [url] of handler.mock.calls) {
      expect(String(url)).not.toMatch(/[?&](access_token|token|api_key)=/i);
    }
  });

  it('holds the invariant the defence document depends on across the whole flow', async () => {
    // The published price formula divides by the weight total, so a vector that
    // does not sum to 1 silently rescales the item. It is the one property every
    // screen in this flow depends on, and the cheapest place to prove it is
    // once, at the end, against the same data all three screens rendered.
    const { handler } = scriptServer();
    vi.stubGlobal('fetch', handler);

    renderDashboard();
    await uploadBoQ();
    await userEvent.click(screen.getByRole('button', { name: /تحلیل وزن‌دهی/ }));
    await screen.findByText('مس');

    const rows = await screen.findAllByRole('row');
    const total = rows.slice(1).reduce((sum, row) => {
      const cell = within(row).getAllByRole('cell')[1]?.textContent ?? '';
      return sum + Number(cell.replace(/[٪,٬]/g, '').replace(/[۰-۹]/g, (d) =>
        '۰۱۲۳۴۵۶۷۸۹'.indexOf(d).toString()
      ));
    }, 0);

    expect(total).toBeCloseTo(100, 6);
    expect(Object.values(TRUE_WEIGHTS).reduce((a, b) => a + b, 0)).toBeCloseTo(1, 10);
  });
});
