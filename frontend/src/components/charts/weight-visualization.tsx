/**
 * The weight breakdown for one item, as a pie or a bar chart beside the table
 * that is the actual evidence.
 *
 * The design decision this file is built around: **the table is the source of
 * truth and the chart is an illustration of it.** Every number the chart draws is
 * also in the table, computed once, by `toChartData` - not by the chart. That is
 * why switching between the pie and the bar cannot change a figure, and why the
 * tests assert the data rather than the SVG: a chart that is rendered from a
 * separately-computed array will eventually disagree with the table, and in a
 * tender document a chart that disagrees with its own table is not a cosmetic
 * defect.
 *
 * The other decision is that a missing value stays missing. `ml_weight` is
 * `null` for an item with no price history, and it is rendered as an em dash
 * rather than as 0%. Those are different statements - "the regression found no
 * copper" versus "we could not run the regression" - and the defense document
 * has to be able to tell them apart, so the chart cannot be the place where they
 * are quietly merged.
 */
'use client';

import * as React from 'react';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import { ABSENT, formatPercent } from '@/lib/persian';
import type { ComponentWeight, WeightBreakdown, WeightSource } from '@/lib/api/queries';

/**
 * One wedge of the chart.
 *
 * `percent` and `mlPercent` are percentages, not fractions. A weight of 0.35 is
 * 35% of the item's price, and charting the raw fraction would draw every wedge
 * as "0.35 units" and label an axis that is not the quantity being measured.
 */
export interface ChartDatum {
  /** Stable across charts, for React keys and for the override form. */
  code: string;
  /** The catalogue id, which is what the override endpoint addresses. */
  componentId: string;
  name: string;
  /** Share of the item's price, as a percentage of the total present. */
  percent: number;
  /** The regression's share as a percentage, or `null` when it did not run. */
  mlPercent: number | null;
  /** The LLM's share as a percentage, or `null` when it declined to answer. */
  llmPercent: number | null;
  source: WeightSource;
  color: string;
}

/**
 * A seven-colour palette, assigned by position in the component catalogue and
 * never by value.
 *
 * Fixed per component rather than per rank, so copper is the same colour in
 * every chart on the page. Assigning by rank would mean a pie and a bar for the
 * same item disagree on colour whenever the ranking differed, and a reader
 * comparing them would reasonably assume they are the same scale.
 *
 * Seven distinct hues for seven wedges: the palette has exactly as many entries
 * as there are components, so no two are ever drawn identically. Recharts
 * defaults to cycling a shorter palette, which is how two materials end up the
 * same shade with nothing in the chart to tell them apart.
 */
export const WEIGHT_COLORS = [
  '#b45309', // مس - copper, the warm metal
  '#475569', // فولاد - steel, slate
  '#a3a3a3', // سیمان - cement, concrete grey
  '#0d9488', // پلیمر - polymer, teal
  '#eab308', // انرژی - energy, yellow
  '#7c3aed', // کار و دستمزد - labour, violet
  '#16a34a', // مصارف عمومی - overhead, green
] as const;

/**
 * Turn a stored weight vector into chart data.
 *
 * Pure, and separated from the drawing for a specific reason: this is where the
 * arithmetic that decides a wedge's size happens, and it is testable exactly
 * without a DOM. The alternative - computing percentages inline in the chart's
 * `data` prop - puts the one number that must be right behind a component that
 * only a screenshot could check.
 *
 * Two details in the normalisation:
 *
 * The total is the sum of what is present, not an assumed 1.0. Stored weights
 * are quantised to four decimal places, so a vector can sum to 0.9999;
 * dividing by 1 would leave a visible hole in the ring and percentages that
 * total 99.99.
 *
 * A zero-weight component is kept. Its wedge is invisible, but dropping the row
 * would remove the reader's only way to see that this item has no steel in it -
 * which is a claim a reviewer may reasonably want to see stated.
 */
export function toChartData(weights: ComponentWeight[]): ChartDatum[] {
  const total = weights.reduce((sum, weight) => sum + (weight.final_weight ?? 0), 0);
  // A vector of all zeros is not a cost breakdown, and dividing by it would
  // produce NaN percentages that render as blanks. The distribution is returned
  // unscaled, which the caller surfaces as an explicit "no attribution" state.
  const scale = total > 0 ? 100 / total : 0;

  return weights.map((weight, index) => ({
    code: weight.component_code,
    componentId: weight.component_id,
    name: weight.component_name_fa,
    percent: (weight.final_weight ?? 0) * scale,
    mlPercent:
      weight.ml_weight === null || weight.ml_weight === undefined
        ? null
        : weight.ml_weight * 100,
    llmPercent:
      weight.llm_weight === null || weight.llm_weight === undefined
        ? null
        : weight.llm_weight * 100,
    source: weight.source,
    color: WEIGHT_COLORS[index % WEIGHT_COLORS.length],
  }));
}

/** The Persian label for a weight's source, as a reviewer reads it. */
const SOURCE_LABELS: Record<WeightSource, string> = {
  llm: 'مدل زبانی',
  ml: 'رگرسیون آماری',
  fusion: 'ادغام دو مدل',
  expert: 'کارشناس',
};

/**
 * A short text alternative for the chart.
 *
 * A pie chart is a picture of angles; a screen reader gets nothing from it. This
 * names the largest component and the confidence, which is the one thing a
 * reader takes from a pie before going to the table anyway.
 */
function describe(data: ChartDatum[], breakdown: WeightBreakdown): string {
  const largest = data.reduce<ChartDatum | null>(
    (best, datum) => (!best || datum.percent > best.percent ? datum : best),
    null
  );
  if (!largest) {
    return 'هیچ وزنی برای این ردیف ثبت نشده است.';
  }
  return (
    `نمودار وزن‌دهی اجزای ${breakdown.description_fa}. ` +
    `بیشترین سهم با ${largest.name} و ${formatPercent(largest.percent / 100)} است.`
  );
}

function WeightPieChart({ data }: { data: ChartDatum[] }) {
  // The chart is an SVG the tests do not assert on - the data behind it is
  // asserted directly in `toChartData`, and a wedge's angle in jsdom is not
  // measurable. So this is a thin Recharts wrapper: it maps the data, sets the
  // colours, and leaves every number to the table beside it.
  const Recharts = require('recharts') as typeof import('recharts');
  const { PieChart, Pie, Cell, Tooltip } = Recharts;

  return (
    <PieChart width={260} height={260}>
      <Pie
        data={data}
        dataKey="percent"
        nameKey="name"
        innerRadius={55}
        outerRadius={100}
        isAnimationActive={false}
      >
        {data.map((datum) => (
          <Cell key={datum.code} fill={datum.color} />
        ))}
      </Pie>
      <Tooltip
        formatter={(value: number, _name, item) => [
          formatPercent(value / 100),
          (item?.payload as ChartDatum | undefined)?.name ?? '',
        ]}
      />
    </PieChart>
  );
}

function WeightBarChart({ data }: { data: ChartDatum[] }) {
  const Recharts = require('recharts') as typeof import('recharts');
  const { BarChart, Bar, Cell, XAxis, YAxis, Tooltip } = Recharts;

  // Largest first: the order a reader scans a bar chart in, and the order that
  // makes the ranking legible without reading a single label.
  const ordered = [...data].sort((a, b) => b.percent - a.percent);

  return (
    <BarChart data={ordered} width={320} height={260}>
      <XAxis dataKey="name" tick={{ fontSize: 11 }} />
      <YAxis tickFormatter={(value: number) => formatPercent(value / 100)} width={48} />
      <Tooltip
        formatter={(value: number) => formatPercent(value / 100)}
        cursor={{ fill: 'rgba(0,0,0,0.05)' }}
      />
      <Bar dataKey="percent" isAnimationActive={false} radius={[3, 3, 0, 0]}>
        {ordered.map((datum) => (
          <Cell key={datum.code} fill={datum.color} />
        ))}
      </Bar>
    </BarChart>
  );
}

export interface WeightVisualizationProps {
  breakdown: WeightBreakdown;
  className?: string;
}

export function WeightVisualization({ breakdown, className }: WeightVisualizationProps) {
  const data = React.useMemo(
    () => toChartData(breakdown.weights),
    [breakdown.weights]
  );
  const [view, setView] = React.useState<'pie' | 'bar'>('pie');
  // One id per instance. The dashboard renders a chart per attributed item, so a
  // fixed id would make every chart on the page announce the first one's summary.
  const captionId = React.useId();
  const summaryId = React.useId();
  const description = describe(data, breakdown);

  const unattributed = data.every((datum) => datum.mlPercent === null);
  const unattributedLlm = data.every((datum) => datum.llmPercent === null);

  return (
    <Card className={className}>
      <CardHeader>
        <CardTitle className="text-lg">
          <span className="text-sm text-muted-foreground">{breakdown.code}</span>{' '}
          {breakdown.description_fa}
        </CardTitle>
        <p className="text-sm text-muted-foreground">{breakdown.category}</p>
      </CardHeader>

      <CardContent className="space-y-4">
        {/*
          The disclosure is the point. An item whose weights came from one model
          alone, with no regression behind them, is a weaker claim in a dispute,
          and the dashboard has to say so where the user reads the number rather
          than in a footnote.
        */}
        {unattributed ? (
          <p
            role="status"
            className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
          >
            این ردیف بدون سابقه قیمت کافی برای رگرسیون آماری است؛ وزن‌دهی صرفاً بر پایه
            مدل زبانی محاسبه شده و اطمینان پایین است.
          </p>
        ) : null}
        {unattributedLlm ? (
          <p
            role="status"
            className="rounded-md border border-amber-500/40 bg-amber-500/10 p-3 text-sm"
          >
            مدل زبانی برای این ردیف وزنی پیشنهاد نکرد؛ وزن‌دهی بر پایه رگرسیون آماری است.
          </p>
        ) : null}

        <div className="flex flex-wrap items-start gap-6">
          {/*
            The name is wired to the visible caption and the description to the
            generated summary, as two separate ids, so a screen reader hears
            "نمودار وزن‌دهی اجزای کابل..." and then "بیشترین سهم با مس و ۳۵٪ است"
            rather than the same sentence twice.

            `aria-labelledby` is needed despite the visible caption: a
            `<figcaption>` does not name its `<figure>` in the accessibility
            tree, so without this the figure is announced as an unlabelled
            group and the summary never gets a context to attach to.

            Both ids are generated rather than fixed because the dashboard renders
            one of these per attributed item, and a hardcoded id would give every
            chart on the page the same name and the same description.
          */}
          <figure
            aria-labelledby={captionId}
            aria-describedby={summaryId}
            className="m-0"
          >
            <figcaption id={captionId} className="text-sm font-medium">
              نمودار وزن‌دهی اجزای {breakdown.description_fa}
            </figcaption>
            <p id={summaryId} className="sr-only">
              {description}
            </p>
            <div
              role="img"
              aria-label={`وزن‌دهی اجزای ${breakdown.description_fa}`}
              className="flex items-center justify-center"
            >
              {view === 'pie' ? <WeightPieChart data={data} /> : <WeightBarChart data={data} />}
            </div>
          </figure>

          <div className="flex flex-col gap-2">
            <div role="tablist" aria-label="نوع نمودار" className="flex gap-1">
              <button
                type="button"
                role="tab"
                aria-selected={view === 'pie'}
                onClick={() => setView('pie')}
                className="rounded-md border px-3 py-1 text-sm"
              >
                نمودار دایره‌ای
              </button>
              <button
                type="button"
                role="tab"
                aria-selected={view === 'bar'}
                onClick={() => setView('bar')}
                className="rounded-md border px-3 py-1 text-sm"
              >
                نمودار میله‌ای
              </button>
            </div>

            {/*
              The table. Every figure the chart shows is here, alongside the
              machine's own number and the source, so a reviewer can compare the
              AI figure with the final one - which FR-028 requires, and which is
              impossible if only the final figure is on screen.
            */}
            <table className="w-full text-sm">
              <caption className="sr-only">جزئیات وزن‌دهی اجزای ردیف</caption>
              <thead>
                <tr className="border-b text-right text-xs text-muted-foreground">
                  <th scope="col" className="py-1">جزء</th>
                  <th scope="col" className="py-1">سهم نهایی</th>
                  <th scope="col" className="py-1">پیشنهاد مدل زبانی</th>
                  <th scope="col" className="py-1">رگرسیون آماری</th>
                  <th scope="col" className="py-1">منبع</th>
                </tr>
              </thead>
              <tbody>
                {data.map((datum) => {
                  const weight = breakdown.weights.find(
                    (candidate) => candidate.component_code === datum.code
                  );
                  return (
                    <tr key={datum.code} className="border-b last:border-0">
                      <td className="py-1">
                        <span
                          aria-hidden="true"
                          className="ml-2 inline-block h-3 w-3 rounded-sm align-middle"
                          style={{ backgroundColor: datum.color }}
                        />
                        {datum.name}
                      </td>
                      <td className="py-1 font-medium">{formatPercent(datum.percent / 100)}</td>
                      <td className="py-1">
                        {datum.llmPercent === null ? ABSENT : formatPercent(datum.llmPercent / 100)}
                      </td>
                      {/*
                        The regression's own figure, kept separate from the final
                        one and rendered as the em dash when it did not run. A "0%"
                        in this column would be read as a measurement - "the
                        regression found no copper" - where the truth is that there
                        was no price history to fit one. The defense document draws
                        the distinction, so the screen it is previewed on has to as
                        well.
                      */}
                      <td className="py-1">
                        {datum.mlPercent === null ? ABSENT : formatPercent(datum.mlPercent / 100)}
                      </td>
                      <td className="py-1 text-xs text-muted-foreground">
                        {SOURCE_LABELS[datum.source]}
                        {weight?.override_reason ? (
                          <span className="block text-amber-700">{weight.override_reason}</span>
                        ) : null}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
              <tfoot>
                <tr>
                  <th scope="row" className="py-1 text-right text-xs text-muted-foreground">
                    اطمینان ادغام
                  </th>
                  <td colSpan={3} className="py-1 text-xs">
                    {formatPercent(breakdown.fusion_confidence)}
                  </td>
                </tr>
              </tfoot>
            </table>
          </div>
        </div>
      </CardContent>
    </Card>
  );
}
