/**
 * The dashboard: upload, analyse, visualise, price, export.
 *
 * The whole component is really one state machine, and the order of its steps is
 * the part worth being deliberate about.
 *
 * An upload creates a *job*, and every subsequent request is scoped to that job
 * id. Nothing about the analysis or the prices can be fetched before the job
 * exists, and the backend refuses both: `GET /weights/{job}` 404s for a job that
 * has not been analysed, and `GET /prices/{job}` 404s for one with no weights.
 * The dashboard therefore gates each query on the state before it, rather than
 * firing them optimistically and rendering the resulting errors. On a fresh
 * project the alternative is two failed requests and an error banner on a
 * perfectly healthy empty project.
 *
 * The order is also not negotiable from the user's side: the price table is
 * reachable straight after the analysis, without visiting it, because the export
 * is often what diagnoses a pricing problem. Making the price view a mandatory
 * step before exporting would mean the export is unreachable exactly when it is
 * most useful.
 */
'use client';

import * as React from 'react';
import { BarChart2, FileText, Sparkles, Upload } from 'lucide-react';

import { Alert } from '@/components/ui/alert';
import { Button } from '@/components/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '@/components/ui/card';
import {
  DashboardLayout,
  DashboardPanel,
  DashboardTabs,
  type DashboardTab,
} from '@/components/ui/dashboard-layout';
import { UploadInterface } from '@/components/ui/upload-interface';
import { ExportModal } from '@/components/ui/export-modal';
import { WeightVisualization } from '@/components/charts/weight-visualization';
import { PriceComparisonTable } from '@/components/tables/price-comparison';
import {
  useAnalyzeWeights,
  usePriceComparison,
  useWeightAnalysis,
  type BoQUploadResponse,
} from '@/lib/api/queries';
import { formatJalali } from '@/lib/persian';

type TabId = 'upload' | 'weights' | 'prices';

const TABS: DashboardTab[] = [
  { id: 'upload', label: 'بارگذاری و تحلیل', icon: Upload },
  { id: 'weights', label: 'وزن‌دهی', icon: BarChart2 },
  { id: 'prices', label: 'قیمت‌ها', icon: FileText },
];

export function Dashboard() {
  const [boq, setBoq] = React.useState<BoQUploadResponse | null>(null);
  const [analysed, setAnalysed] = React.useState(false);
  const [tab, setTab] = React.useState<TabId>('upload');

  const jobId = boq?.job_id ?? null;
  const projectId = boq?.preview?.project_id ?? null;

  const analyze = useAnalyzeWeights();
  const weights = useWeightAnalysis(jobId, analysed);
  /*
   * The price query is gated on the prices tab as well as on the analysis, not
   * just on the analysis. It is a large response for a full BoQ and nobody has
   * asked for it until the tab is opened, so fetching it on analysis would spend
   * the request on a view that is then discarded.
   */
  const prices = usePriceComparison(jobId, analysed && tab === 'prices');

  const analysisFailed = analyze.error instanceof Error ? analyze.error.message : null;
  const weightsError = weights.error instanceof Error ? weights.error.message : null;
  const pricesError = prices.error instanceof Error ? prices.error.message : null;

  async function runAnalysis() {
    if (!jobId || !projectId) {
      return;
    }
    setAnalysed(false);
    try {
      await analyze.mutateAsync({ job_id: jobId, project_id: projectId });
      // The mutation's response is a receipt, not the weights. The weights come
      // from a read that has to observe the completed analysis, so the tab moves
      // to the view that will make that read and the query takes over.
      setAnalysed(true);
      setTab('weights');
    } catch {
      // Rendered from `analyze.error`; the server's message is the actionable
      // part and re-wording it here would only lose information.
      setAnalysed(false);
    }
  }

  return (
    <DashboardLayout
      title="داشبورد"
      subtitle={
        boq
          ? `کار ${boq.job_id}${boq.preview ? ` — ${boq.preview.total_chapters} فصل` : ''}`
          : undefined
      }
      nav={<DashboardTabs tabs={TABS} active={tab} onChange={(id) => setTab(id as TabId)} />}
      actions={<ExportModal jobId={jobId} projectId={projectId} />}
    >
      <DashboardPanel id="upload" active={tab}>
        <div className="space-y-4">
          {!boq ? (
            <p className="text-sm text-muted-foreground">
              ابتدا یک فایل BoQ بارگذاری کنید تا تحلیل وزن‌دهی، محاسبه قیمت و خروجی گرفتن
              فعال شود.
            </p>
          ) : null}

          <UploadInterface onUploaded={setBoq} />

          {boq ? (
            <Card>
              <CardHeader>
                <CardTitle className="text-lg">تحلیل وزن‌دهی</CardTitle>
              </CardHeader>
              <CardContent className="space-y-3">
                <p className="text-sm text-muted-foreground">
                  وزن‌دهی هر ردیف از دو منبع ادغام می‌شود: تحلیل معنایی شرح کالا توسط مدل
                  زبانی و رگرسیون آماری روی شاخص‌های بازار.
                </p>
                <Button
                  type="button"
                  onClick={() => void runAnalysis()}
                  disabled={!projectId || analyze.isPending}
                >
                  <Sparkles className="ml-2 h-4 w-4" aria-hidden="true" />
                  تحلیل وزن‌دهی
                </Button>
                {boq.preview ? (
                  <p className="text-xs text-muted-foreground">
                    آخرین به‌روزرسانی شاخص‌ها: {formatJalali(new Date().toISOString(), true)}
                  </p>
                ) : null}
              </CardContent>
            </Card>
          ) : null}

          {analysisFailed ? (
            <Alert tone="error">{analysisFailed}</Alert>
          ) : null}
        </div>
      </DashboardPanel>

      <DashboardPanel id="weights" active={tab}>
        {weights.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">
            در حال محاسبه وزن‌دهی…
          </p>
        ) : null}

        {weightsError ? <Alert tone="error">{weightsError}</Alert> : null}

        {weights.data?.warnings.length ? (
          <Alert tone="warning" className="mb-4">
            <ul className="list-inside list-disc space-y-1">
              {weights.data.warnings.map((warning) => (
                <li key={warning}>{warning}</li>
              ))}
            </ul>
          </Alert>
        ) : null}

        {weights.data?.items.map((breakdown) => (
          <WeightVisualization
            key={breakdown.item_id}
            breakdown={breakdown}
            className="mb-4"
          />
        ))}

        {weights.data && weights.data.items.length === 0 ? (
          <p className="text-sm text-muted-foreground">
            برای هیچ‌یک از ردیف‌ها وزن‌دهی انجام نشد.
          </p>
        ) : null}
      </DashboardPanel>

      <DashboardPanel id="prices" active={tab}>
        {prices.isPending ? (
          <p role="status" className="text-sm text-muted-foreground">
            در حال محاسبه قیمت‌ها…
          </p>
        ) : null}        {pricesError ? <Alert tone="error">{pricesError}</Alert> : null}
        {prices.data ? <PriceComparisonTable comparison={prices.data} /> : null}
        {/*
          The empty state is stated rather than left blank. A heading with no
          table under it reads as a load failure, and the reader's next move -
          re-uploading the file - is the wrong one.
        */}
        {!prices.isPending && !prices.data && !pricesError ? (
          <p className="text-sm text-muted-foreground">
            {analysed
              ? 'قیمتی برای نمایش وجود ندارد.'
              : 'ابتدا تحلیل وزن‌دهی را انجام دهید تا قیمت‌ها محاسبه شود.'}
          </p>
        ) : null}
      </DashboardPanel>
    </DashboardLayout>
  );
}
