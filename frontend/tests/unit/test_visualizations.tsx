/**
 * Weight visualisation tests (US7, T096).
 *
 * Recharts draws to SVG, and a screenshot of a chart is not a test: it changes
 * with a font load, and it cannot say *why* a wedge is the wrong size. So the
 * assertions here are on the data the chart is given and on the text a reader
 * actually reads - the labelled table beneath the graphic, the source badges,
 * the confidence disclosure - which is the part a tender reviewer relies on and
 * the part a re-render could silently get wrong.
 *
 * The data preparation is separated from the drawing on purpose.
 * `toChartData` is pure, so the arithmetic that decides a wedge's size can be
 * asserted exactly, without a DOM. The rendering tests then cover what the
 * reader sees.
 */
import { describe, it, expect, vi, afterEach } from 'vitest';
import { render, screen, within } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import type { ReactElement } from 'react';

import {
  WeightVisualization,
  toChartData,
  WEIGHT_COLORS,
} from '@/components/charts/weight-visualization';
import {
  COMPONENT_CODES,
  COMPONENT_IDS,
  TRUE_WEIGHTS,
  componentWeights,
  unattributedBreakdown,
  weightBreakdown,
} from '../fixtures/analysis';

function withQueryClient(ui: ReactElement) {
  const client = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  return render(<QueryClientProvider client={client}>{ui}</QueryClientProvider>);
}

afterEach(() => {
  vi.restoreAllMocks();
});

describe('toChartData', () => {
  it('emits one slice per component, in the canonical order', () => {
    const data = toChartData(weightBreakdown().weights);

    expect(data.map((d) => d.code)).toEqual([...COMPONENT_CODES]);
  });

  it('reports the weight as a percentage of the item price', () => {
    // A weight of 0.35 is 35% of the price. Charting the fraction would render
    // every wedge as 0.35 units and every axis as a lie.
    const data = toChartData(weightBreakdown().weights);

    expect(data.find((d) => d.code === 'copper')!.percent).toBeCloseTo(35, 6);
  });

  it('normalises against the total actually present, not against an assumed 1.0', () => {
    // A stored vector is quantised to four decimal places, so it can sum to
    // 0.9999. Dividing by 1.0 would leave the pie a 0.01% hole; dividing by the
    // real total keeps the ring closed and the percentages honest.
    const weights = componentWeights();
    const total = weights.reduce((sum, w) => sum + w.final_weight, 0);
    expect(total).toBeCloseTo(1, 10);

    const data = toChartData(weights);
    expect(data.reduce((sum, d) => sum + d.percent, 0)).toBeCloseTo(100, 6);
  });

  it('drops a zero-weight component from the chart but keeps it in the table', () => {
    // A zero slice is invisible but still occupies a legend row, and "this item
    // has no steel in it" is a claim a reviewer may well want to see stated.
    const weights = componentWeights({ steel: 0 });

    const data = toChartData(weights);

    expect(data.find((d) => d.code === 'steel')!.percent).toBe(0);
    expect(data).toHaveLength(COMPONENT_CODES.length);
  });

  it('carries the Persian component name through, not the code', () => {
    // The chart is read by people who do not know that "polymer" is the code.
    const data = toChartData(weightBreakdown().weights);

    expect(data.find((d) => d.code === 'copper')!.name).toBe('مس');
  });

  it('keeps a null ML weight as null rather than coercing it to zero', () => {
    // Zero is a measurement. "We did not measure this" is a different statement,
    // and the defence document's whole job is to keep the two apart.
    const data = toChartData(unattributedBreakdown().weights);

    expect(data.every((d) => d.mlPercent === null)).toBe(true);
  });

  it('gives every component a colour, and never the same colour twice', () => {
    // Seven wedges in a palette of six means two components are drawn
    // identically, and a reader comparing the table to the graphic has no way to
    // tell which is which.
    const data = toChartData(weightBreakdown().weights);
    const colors = data.map((d) => d.color);

    expect(new Set(colors).size).toBe(data.length);
    expect(WEIGHT_COLORS).toHaveLength(COMPONENT_CODES.length);
  });

  it('assigns a component the same colour in every chart on the page', () => {
    // A pie and a bar for the same item that disagree on colour is worse than
    // no second chart: the reader assumes they are the same scale.
    const first = toChartData(weightBreakdown().weights);
    const second = toChartData(weightBreakdown({ copper: 0.5 }).weights);

    for (const code of COMPONENT_CODES) {
      expect(first.find((d) => d.code === code)!.color).toBe(
        second.find((d) => d.code === code)!.color
      );
    }
  });
});

describe('WeightVisualization', () => {
  it('lists every component with its share of the item price', () => {
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    const row = screen.getByText('مس').closest('tr')!;
    expect(within(row).getByText('۳۵٪')).toBeInTheDocument();
  });

  it('names all seven components', () => {
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    for (const name of ['مس', 'فولاد', 'سیمان', 'پلیمر', 'انرژی', 'کار و دستمزد', 'مصارف عمومی']) {
      expect(screen.getByText(name)).toBeInTheDocument();
    }
  });

  it('shows the AI figure beside the final one, so the expert can compare', () => {
    // FR-028. The document's whole point is that a human departed from a
    // machine's number, on the record - which is impossible to argue if only the
    // final figure is on screen.
    //
    // The override moves 0.15 from steel to copper rather than adding 0.15 to
    // copper, so the vector still sums to 1.0. A vector that did not would be
    // renormalised against its own total, and the final share would display as
    // 43% rather than the 50% the override asked for - which would make this
    // test assert the normalisation instead of the comparison it is about.
    withQueryClient(
      <WeightVisualization
        breakdown={weightBreakdown({ copper: 0.5, steel: 0.1 }, { llmWeights: { copper: 0.7 } })}
      />
    );

    const row = screen.getByText('مس').closest('tr')!;
    expect(within(row).getByText('۷۰٪')).toBeInTheDocument();
    expect(within(row).getByText('۵۰٪')).toBeInTheDocument();
    // And the regression's third opinion, which is neither of the two above.
    expect(within(row).getByText('۳۱٪')).toBeInTheDocument();
  });

  it('marks an expert override as such, with its reason', () => {
    const weights = componentWeights({ copper: 0.5, steel: 0.1 });
    const copper = weights.find((w) => w.component_code === 'copper')!;
    copper.source = 'expert';
    copper.override_reason = 'قیمت مس در بازار جهانی افزایش یافت';

    withQueryClient(
      <WeightVisualization breakdown={{ ...weightBreakdown(), weights }} />
    );

    expect(screen.getByText('قیمت مس در بازار جهانی افزایش یافت')).toBeInTheDocument();
    expect(screen.getByText(/کارشناس/)).toBeInTheDocument();
  });

  it('shows an absent ML figure as undisclosed, not as 0%', () => {
    withQueryClient(<WeightVisualization breakdown={unattributedBreakdown()} />);

    // The row must not contain a 0% that a reviewer would read as "the
    // regression found no copper in this item".
    const row = screen.getByText('مس').closest('tr')!;
    expect(within(row).queryByText('۰٪')).not.toBeInTheDocument();
    expect(within(row).getByText('—')).toBeInTheDocument();
  });

  it('discloses the low confidence of an item only the LLM answered', () => {
    withQueryClient(<WeightVisualization breakdown={unattributedBreakdown()} />);

    expect(screen.getByRole('status')).toHaveTextContent(/بدون سابقه قیمت/);
  });

  it('does not warn about confidence on a well-evidenced item', () => {
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    expect(screen.queryByRole('status')).not.toBeInTheDocument();
  });

  it('shows the fused confidence as a percentage of its own scale', () => {
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    expect(screen.getByText(/۸۸٪/)).toBeInTheDocument();
  });

  it('switches between the pie and the bar view', async () => {
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    const toBar = screen.getByRole('tab', { name: 'نمودار میله‌ای' });
    await userEvent.click(toBar);

    expect(toBar).toHaveAttribute('aria-selected', 'true');
    expect(screen.getByRole('tab', { name: 'نمودار دایره‌ای' })).toHaveAttribute(
      'aria-selected',
      'false'
    );
  });

  it('keeps the numbers identical between the two views', async () => {
    // The chart is the illustration; the table is the evidence. If the bar view
    // rendered a different figure the two would contradict each other and the
    // reviewer could not tell which one the defence document is quoting.
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    const readShares = () =>
      screen
        .getAllByRole('row')
        .slice(1)
        .map((row) => within(row).getAllByRole('cell')[1]?.textContent);

    const asPie = readShares();
    await userEvent.click(screen.getByRole('tab', { name: 'نمودار میله‌ای' }));

    expect(readShares()).toEqual(asPie);
  });

  it('exposes the chart to assistive technology with a name and a summary', () => {
    withQueryClient(<WeightVisualization breakdown={weightBreakdown()} />);

    // A pie chart is unreadable to a screen reader without a text alternative,
    // and the table below is not labelled as the chart's equivalent.
    const figure = screen.getByRole('figure');
    expect(figure).toHaveAccessibleName(/وزن‌دهی اجزای/);
    expect(figure).toHaveAccessibleDescription(/مس/);
  });

  it('sorts a bar view largest first, which is the order a reader scans', () => {
    const data = toChartData(weightBreakdown().weights);
    const descending = [...data].sort((a, b) => b.percent - a.percent);

    expect(descending.map((d) => d.code).slice(0, 2)).toEqual(['copper', 'steel']);
  });
});

describe('weight semantics the chart must not blur', () => {
  it('keeps a source of "fusion" distinct from "expert" in the data it renders', () => {
    // The whole point of the audit trail is that a human's decision is
    // distinguishable from the machine's. A chart that colours both "high
    // confidence" is exactly the confusion FR-028 exists to prevent.
    const fused = weightBreakdown().weights;
    const expert = componentWeights();
    expert.find((w) => w.component_code === 'copper')!.source = 'expert';

    expect(toChartData(fused).find((d) => d.code === 'copper')!.source).toBe('fusion');
    expect(toChartData(expert).find((d) => d.code === 'copper')!.source).toBe('expert');
  });

  it('does not invent an ML figure for an item the regression could not fit', () => {
    const data = toChartData(unattributedBreakdown().weights);

    expect(data.every((d) => d.mlPercent === null)).toBe(true);
    expect(data.map((d) => d.source)).toEqual(
      COMPONENT_CODES.map(() => 'llm')
    );
  });

  it('keeps the true cost structure recoverable from the chart data', () => {
    // The end-to-end guarantee: chart data for the known cost structure is the
    // known cost structure, so a rounding or ordering slip in toChartData shows
    // up here rather than as a subtly wrong pie.
    const data = toChartData(weightBreakdown().weights);

    for (const code of COMPONENT_CODES) {
      expect(data.find((d) => d.code === code)!.percent / 100).toBeCloseTo(
        TRUE_WEIGHTS[code],
        6
      );
    }
    expect(data.reduce((sum, d) => sum + d.percent, 0)).toBeCloseTo(100, 6);
  });

  it('gives the copper slice the same id the override endpoint expects', () => {
    // A chart that identifies a component by its code cannot drive the override
    // form, which the API addresses by id.
    const data = toChartData(weightBreakdown().weights);

    expect(data.find((d) => d.code === 'copper')!.componentId).toBe(
      COMPONENT_IDS.copper
    );
  });
});
