/**
 * The dashboard's data layer: the request shapes, and the queries over them.
 *
 * The query keys are the load-bearing part of this file. TanStack Query caches by
 * key, so a key that does not include everything the response depends on will
 * serve a stale answer to a question that changed - and on this dashboard that
 * is a tender document showing one item's weights next to another's prices. The
 * rule applied throughout is that a key names *the thing asked about*, and the
 * scope of the analysis is part of that thing, not a filter applied afterwards.
 *
 * The two-phase job model is why the keys nest the way they do. An analysis is
 * asynchronous: `POST /weights/analyze` returns immediately with a job id, and
 * the weights are read from a separate endpoint. So the analysis status is one
 * query and the weights are another, and the second must not be enabled until
 * the first has completed - which is what `enabled` on the weights query is for.
 */
import {
  useMutation,
  useQuery,
  useQueryClient,
  type UseQueryOptions,
} from '@tanstack/react-query';
import { request, type RequestOptions } from './client';

// --- Response shapes --------------------------------------------------------
//
// Transcribed from the backend's Pydantic models - `BoQUploadResponse`,
// `BoQPreview`, `WeightBreakdown`, `PriceComparison`, `ExportExcelResponse`,
// `ExportDefenseDocResponse`. A dashboard written against a guessed shape passes
// its own tests and fails against the server, so these are the contract.

export interface BoQItemPreview {
  item_id: string;
  code: string;
  description_fa: string;
  unit: string;
  base_price: number;
  quantity: number;
  row_number: number | null;
}

export interface ChapterPreview {
  chapter_id: string;
  code: string;
  name: string;
  item_count: number;
  items: BoQItemPreview[];
}

export interface BoQPreview {
  project_id: string;
  total_items: number;
  total_chapters: number;
  chapters: ChapterPreview[];
  warnings: string[];
}

export interface BoQUploadResponse {
  job_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  preview: BoQPreview | null;
  message: string | null;
}

/** Where a component's number came from. `expert` means a human, on the record. */
export type WeightSource = 'llm' | 'ml' | 'fusion' | 'expert';

export interface ComponentWeight {
  component_id: string;
  component_code: string;
  component_name_fa: string;
  /**
   * The LLM's share, or `null` when the parser declined to answer.
   *
   * Nullable rather than zero because "the model had nothing to say" and "the
   * model said zero" are different claims, and the defense document has to be
   * able to keep them apart.
   */
  llm_weight: number | null;
  /** The regression's share, or `null` when there was too little history to fit one. */
  ml_weight: number | null;
  final_weight: number;
  source: WeightSource;
  confidence: number | null;
  ml_r2: number | null;
  llm_certainty: number | null;
  overridden_by: string | null;
  overridden_at: string | null;
  override_reason: string | null;
}

export interface WeightBreakdown {
  item_id: string;
  code: string;
  description_fa: string;
  category: string;
  llm_confidence: number | null;
  weights: ComponentWeight[];
  fusion_confidence: number;
  updated_at: string;
}

export interface WeightAnalysisResponse {
  job_id: string;
  status: 'pending' | 'running' | 'completed' | 'failed';
  message: string | null;
}

export interface WeightAnalysisPayload {
  job_id: string;
  boq_job_id: string;
  project_id: string;
  status: string;
  item_count: number;
  skipped: number;
  fusion_alpha: number;
  items: WeightBreakdown[];
  summary: {
    job_id: string;
    item_count: number;
    items_with_invalid_weight_sum: string[];
  };
  failures: unknown[];
  warnings: string[];
}

export interface PriceAdjustments {
  risk_buffer: number;
  payment_terms: number;
  profit_margin: number;
}

export interface PriceResult {
  item_id: string;
  code: string;
  description_fa: string;
  unit: string;
  base_price: number;
  updated_price: number;
  final_price: number;
  adjustments: PriceAdjustments;
  index_snapshot: Record<string, unknown>;
  calculated_at: string;
}

export interface PriceComparison {
  items: PriceResult[];
  summary: Record<string, unknown>;
}

export interface ExportExcelResponse {
  download_url: string;
  expires_at: string;
  filename: string;
}

export interface ExportDefenseDocResponse {
  download_url: string;
  expires_at: string;
  filename: string;
}

// --- Request shapes --------------------------------------------------------

export interface WeightAnalysisRequest {
  job_id: string;
  project_id: string;
  item_ids?: string[] | null;
  force_reanalyze?: boolean;
}

export interface PriceCalculationRequest {
  job_id: string;
  project_id: string;
  item_ids?: string[] | null;
}

export interface ExportExcelRequest {
  job_id: string;
  project_id: string;
  include_weights: boolean;
  include_forecasts: boolean;
  include_comparison: boolean;
}

export interface ExportDefenseDocRequest {
  job_id: string;
  project_id: string;
  item_ids?: string[] | null;
  chapter_ids?: string[] | null;
  format: 'pdf' | 'html';
}

/** Which sheets the Excel export should contain. */
export interface ExportSheetOptions {
  includeWeights?: boolean;
  includeForecasts?: boolean;
  includeComparison?: boolean;
}

/**
 * Build the `/exports/excel` body.
 *
 * Exported and tested on its own because it is the one place where a wrong
 * shape is invisible until a click. `ExportExcelRequest` is a Pydantic model
 * with `extra` forbidden, so an extra key is a 422 - and a 422 at click time,
 * after the user has picked their sheets, is a poor way to learn that a
 * convenience wrapper had renamed a field.
 *
 * The defaults are all true, and that is a deliberate reading of the
 * quickstart: Scenario 6's validation checklist lists the weights, comparison
 * and forecast sheets as required output, so an export that omitted one would
 * fail acceptance while appearing to have worked.
 */
export function buildExcelExportPayload(
  jobId: string,
  projectId: string,
  options: ExportSheetOptions = {}
): ExportExcelRequest {
  return {
    job_id: jobId,
    project_id: projectId,
    include_weights: options.includeWeights ?? true,
    include_forecasts: options.includeForecasts ?? true,
    include_comparison: options.includeComparison ?? true,
  };
}

// --- Query keys ------------------------------------------------------------

/**
 * Query keys, as nested tuples.
 *
 * The BoQ job id is the root because everything else in the flow is scoped to
 * one upload. Including it at every level means uploading a second file cannot
 * serve the first file's analysis out of the cache - the failure that produces a
 * dashboard describing a tender the user is not working on.
 */
export const queryKeys = {
  boqPreview: (jobId: string) => ['boq', 'preview', jobId] as const,
  boqItems: (jobId: string, page: number, pageSize: number) =>
    ['boq', 'items', jobId, page, pageSize] as const,
  weightAnalysis: (jobId: string) => ['weights', jobId] as const,
  priceComparison: (jobId: string) => ['prices', jobId] as const,
  exportResult: (jobId: string, kind: 'excel' | 'defense-doc') =>
    ['exports', kind, jobId] as const,
};

// --- Queries ---------------------------------------------------------------

/**
 * The parsed BoQ for an uploaded job.
 *
 * `enabled` on the job id rather than a guard in the component: a query for
 * `undefined` would key on the literal string "undefined" and could be served
 * from a previous component's cache, so the job id is part of the key and the
 * query simply does not run until there is one.
 */
export function useBoqPreview(jobId: string | null) {
  return useQuery({
    queryKey: queryKeys.boqPreview(jobId ?? 'none'),
    queryFn: ({ signal }): Promise<BoQPreview> =>
      request(`/boq/${jobId}/preview`, { signal }),
    enabled: Boolean(jobId),
  });
}

/**
 * The weight attribution for an analysed job.
 *
 * Gated on the analysis having completed, which the caller supplies as
 * `analysisComplete`. Fetching before then is not merely wasteful: the endpoint
 * 404s for a job that has not been analysed, so the dashboard would show a
 * failure for a state the user is about to leave.
 */
export function useWeightAnalysis(
  jobId: string | null,
  analysisComplete: boolean,
  options: Partial<UseQueryOptions<WeightAnalysisPayload>> = {}
) {
  return useQuery({
    queryKey: queryKeys.weightAnalysis(jobId ?? 'none'),
    queryFn: ({ signal }): Promise<WeightAnalysisPayload> =>
      request(`/weights/${jobId}`, { signal }),
    enabled: Boolean(jobId) && analysisComplete,
    ...options,
  });
}

/** The recalculated prices, likewise gated on the weights existing. */
export function usePriceComparison(
  jobId: string | null,
  weightsReady: boolean
) {
  return useQuery({
    queryKey: queryKeys.priceComparison(jobId ?? 'none'),
    queryFn: ({ signal }): Promise<PriceComparison> =>
      request(`/prices/${jobId}`, { signal }),
    enabled: Boolean(jobId) && weightsReady,
  });
}

// --- Mutations -------------------------------------------------------------

/**
 * Upload a BoQ spreadsheet.
 *
 * Not a query: it creates the job that every other query in the flow is scoped
 * to, so it invalidates the whole `boq` namespace on success. The upload is the
 * one mutation that changes what "the current BoQ" means.
 *
 * Posted to `/api/upload` - the route handler in `app/api/(dashboard)/upload` -
 * rather than to the backend path the rewrite would map. Everything else in this
 * file goes through the rewrite; the upload has a handler because it is the one
 * request whose body is too large to want buffered in an intermediary, and the
 * handler streams it. See that file for the reasoning.
 */
export function useUploadBoQ() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: async (file: File): Promise<BoQUploadResponse> => {
      const form = new FormData();
      form.append('file', file);
      const options: RequestOptions = { method: 'POST', form };
      return request<BoQUploadResponse>('/upload', options);
    },
    onSuccess: () => client.invalidateQueries({ queryKey: ['boq'] }),
  });
}

/**
 * Run the weight attribution.
 *
 * On success the weights cache is cleared rather than refetched. The mutation's
 * response is `{job_id, status}` - a receipt, not the data - and the weights
 * then come from a read that has to observe the completed analysis, so
 * invalidating is the honest way to say "whatever is cached is now stale".
 */
export function useAnalyzeWeights() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: WeightAnalysisRequest): Promise<WeightAnalysisResponse> =>
      request('/weights/analyze', { method: 'POST', body: payload }),
    onSuccess: (_result, variables) => {
      void client.removeQueries({
        queryKey: queryKeys.weightAnalysis(variables.job_id),
      });
    },
  });
}

/** Recalculate prices for a job. */
export function useRecalculatePrices() {
  const client = useQueryClient();
  return useMutation({
    mutationFn: (payload: PriceCalculationRequest): Promise<{ job_id: string; status: string }> =>
      request('/prices/recalculate', { method: 'POST', body: payload }),
    onSuccess: () =>
      client.invalidateQueries({ queryKey: ['prices'] }),
  });
}

/**
 * Generate the Excel workbook.
 *
 * The result is a signed, expiring URL rather than bytes, so this mutation's
 * `data` is a link the user follows. Keeping it out of the query cache is
 * deliberate: a cached download URL that expired would render a download button
 * that 410s, which looks like a broken application rather than an expired link.
 */
export function useExportExcel() {
  return useMutation({
    mutationFn: (payload: ExportExcelRequest): Promise<ExportExcelResponse> =>
      request('/exports/excel', { method: 'POST', body: payload }),
  });
}

/** Generate the defense document (سند دفاعیه). */
export function useExportDefenseDoc() {
  return useMutation({
    mutationFn: (payload: ExportDefenseDocRequest): Promise<ExportDefenseDocResponse> =>
      request('/exports/defense-doc', { method: 'POST', body: payload }),
  });
}
