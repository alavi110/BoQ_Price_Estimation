/**
 * Shared payload builders for the dashboard tests.
 *
 * The shapes here are transcribed from the backend's Pydantic response models -
 * `BoQPreview`, `WeightBreakdown`, `PriceResult`, `ExportExcelResponse` - rather
 * than invented. A dashboard test is only worth writing against the real
 * contract; a fixture with a plausible-but-wrong shape passes against a
 * component that is also wrong, and the two errors cancel.
 *
 * The numbers are chosen to make the assertions meaningful rather than round.
 * There are three weight vectors here, not one, and they are all different:
 * `TRUE_WEIGHTS` is the cost structure the item was generated from,
 * `ML_WEIGHTS` is what the regression makes of it, and `LLM_WEIGHTS` is what the
 * language model makes of it. Only the first is the truth, and the fused
 * `final_weight` lands on it without being equal to either input. A fixture that
 * set all three to the same value would make the side-by-side comparison FR-028
 * requires vacuous - there would be nothing to compare, and a component that
 * quietly dropped the model's own figure would still pass.
 *
 * Every vector sums to 1.0, the final weights sum to 0.35 rather than to an
 * equal split so a component rendering the uniform 1/7 fallback is
 * distinguishable, copper is the largest share so a component that reverses the
 * ranking is distinguishable, and the base prices are not round numbers so a
 * formatter that rounds to "nice" figures is distinguishable.
 *
 * The item codes are `001-001` and `001-002` for the reason the backend's
 * `_extract_chapter_code` gives: an Iranian BoQ code is `<chapter><item>` with
 * the chapter as the leading *three* digits, and anything that does not start
 * with three digits falls back to chapter `"000"` rather than raising. So a
 * two-digit chapter like `01-001` is not a code the parser could ever emit - a
 * fixture built on one would describe a BoQ that silently lands in a single
 * fallback chapter, and every assertion about chapter grouping would be about a
 * file that does not exist.
 */

export const COMPONENT_CODES = [
  'copper',
  'steel',
  'cement',
  'polymer',
  'energy',
  'labor',
  'overhead',
] as const;

export type ComponentCode = (typeof COMPONENT_CODES)[number];

/** The cost structure the fixture item was generated from. */
export const TRUE_WEIGHTS: Record<ComponentCode, number> = {
  copper: 0.35,
  steel: 0.25,
  cement: 0.08,
  polymer: 0.07,
  energy: 0.1,
  labor: 0.1,
  overhead: 0.05,
};

const PERSIAN_NAMES: Record<ComponentCode, string> = {
  copper: 'مس',
  steel: 'فولاد',
  cement: 'سیمان',
  polymer: 'پلیمر',
  energy: 'انرژی',
  labor: 'کار و دستمزد',
  overhead: 'مصارف عمومی',
};

/**
 * The regression's own answer, which is *not* the truth and is not the final one.
 *
 * This vector is deliberately skewed the way a real one is. A regression on
 * market indices can only see components with a price series, so it credits
 * copper and steel heavily, reads the energy index as a proxy for polymer, and
 * returns almost nothing for labour - which has no index at all. It is not a
 * worse answer than the LLM's, it is a differently-blind one, and that is what
 * makes the two worth fusing.
 */
export const ML_WEIGHTS: Record<ComponentCode, number> = {
  copper: 0.31,
  steel: 0.29,
  cement: 0.11,
  polymer: 0.09,
  energy: 0.14,
  labor: 0.02,
  overhead: 0.04,
};

/**
 * The language model's answer, which reads the item's description rather than
 * the market.
 *
 * Skewed the other way: it anchors on whatever the description names first, so
 * it over-credits copper and labour and under-credits the materials the text
 * mentions in passing. `llm_certainty` is correspondingly lower than the
 * regression's `ml_r2` - the model is confident in a reading nobody can check.
 *
 * Note these two vectors sum to 1.0 each, but `final_weight` is the fused result
 * and need not equal either. That gap is the whole point of the audit trail, so
 * the fixture does not collapse it.
 */
export const LLM_WEIGHTS: Record<ComponentCode, number> = {
  copper: 0.42,
  steel: 0.18,
  cement: 0.05,
  polymer: 0.06,
  energy: 0.11,
  labor: 0.12,
  overhead: 0.06,
};

export interface ComponentWeightPayload {
  component_id: string;
  component_code: string;
  component_name_fa: string;
  llm_weight: number | null;
  ml_weight: number | null;
  final_weight: number;
  source: 'llm' | 'ml' | 'fusion' | 'expert';
  confidence: number | null;
  ml_r2: number | null;
  llm_certainty: number | null;
  overridden_by: string | null;
  overridden_at: string | null;
  override_reason: string | null;
}

/** A stable UUID per component code, so overrides can reference one. */
export const COMPONENT_IDS: Record<ComponentCode, string> = {
  copper: '11111111-1111-4111-8111-111111111111',
  steel: '22222222-2222-4222-8222-222222222222',
  cement: '33333333-3333-4333-8333-333333333333',
  polymer: '44444444-4444-4444-8444-444444444444',
  energy: '55555555-5555-4555-8555-555555555555',
  labor: '66666666-6666-4666-8666-666666666666',
  overhead: '77777777-7777-4777-8777-777777777777',
};

export function componentWeights(
  overrides: Partial<Record<ComponentCode, number>> = {},
  options: {
    source?: ComponentWeightPayload['source'];
    /** Drop the ML column, as an item with no price history comes back. */
    withoutMl?: boolean;
    llmWeights?: Partial<Record<ComponentCode, number>>;
  } = {},
): ComponentWeightPayload[] {
  const source = options.source ?? 'fusion';
  return COMPONENT_CODES.map((code) => ({
    component_id: COMPONENT_IDS[code],
    component_code: code,
    component_name_fa: PERSIAN_NAMES[code],
    llm_weight: options.llmWeights?.[code] ?? LLM_WEIGHTS[code],
    ml_weight: options.withoutMl ? null : ML_WEIGHTS[code],
    final_weight: overrides[code] ?? TRUE_WEIGHTS[code],
    source,
    confidence: 0.91,
    ml_r2: options.withoutMl ? null : 0.94,
    llm_certainty: 0.78,
    overridden_by: null,
    overridden_at: null,
    override_reason: null,
  }));
}

export interface WeightBreakdownPayload {
  item_id: string;
  code: string;
  description_fa: string;
  category: string;
  llm_confidence: number | null;
  weights: ComponentWeightPayload[];
  fusion_confidence: number;
  updated_at: string;
}

export const ITEM_ID = 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa';
export const ITEM_ID_2 = 'bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb';
export const PROJECT_ID = 'cccccccc-cccc-4ccc-8ccc-cccccccccccc';
export const JOB_ID = 'dddddddd-dddd-4ddd-8ddd-dddddddddddd';

export function weightBreakdown(
  overrides: Partial<Record<ComponentCode, number>> = {},
  options: Parameters<typeof componentWeights>[1] = {},
): WeightBreakdownPayload {
  return {
    item_id: ITEM_ID,
    code: '001-001',
    description_fa: 'کابل تحت زمینی فشار قوی ۲۰ کیلومتر',
    category: 'کابل برق',
    llm_confidence: options.withoutMl ? 0.3 : 0.78,
    weights: componentWeights(overrides, options),
    fusion_confidence: options.withoutMl ? 0.24 : 0.88,
    updated_at: '2026-09-27T08:30:00Z',
  };
}

/** The `GET /weights/{job_id}` payload, with one item attributed. */
export function weightAnalysisPayload(
  items: WeightBreakdownPayload[] = [weightBreakdown()],
) {
  return {
    job_id: JOB_ID,
    boq_job_id: JOB_ID,
    project_id: PROJECT_ID,
    status: 'completed',
    item_count: items.length,
    skipped: 0,
    fusion_alpha: 0.7,
    items,
    summary: {
      job_id: JOB_ID,
      item_count: items.length,
      items_with_invalid_weight_sum: [],
    },
    failures: [],
    warnings: [],
  };
}

/** An item with no price history: LLM-only, and disclosed as such. */
export function unattributedBreakdown(): WeightBreakdownPayload {
  return weightBreakdown({}, { withoutMl: true, source: 'llm' });
}

/** The `GET /prices/{job_id}` payload - `PriceComparison`. */
export function priceComparisonPayload() {
  return {
    items: [
      {
        item_id: ITEM_ID,
        code: '001-001',
        description_fa: 'کابل تحت زمینی فشار قوی ۲۰ کیلومتر',
        unit: 'کیلوگرم',
        base_price: 1_250_000,
        updated_price: 1_437_500,
        final_price: 1_710_625,
        adjustments: { risk_buffer: 1.04, payment_terms: 1.08, profit_margin: 1.1 },
        index_snapshot: { copper: { base: 100, current: 110 } },
        calculated_at: '2026-09-27T08:30:00Z',
      },
      {
        item_id: ITEM_ID_2,
        code: '001-002',
        description_fa: 'کابل فولادی روکش دار',
        unit: 'متر',
        base_price: 875_400,
        updated_price: 900_000,
        final_price: 1_071_600,
        adjustments: { risk_buffer: 1.04, payment_terms: 1.08, profit_margin: 1.1 },
        index_snapshot: { steel: { base: 200, current: 190 } },
        calculated_at: '2026-09-27T08:30:00Z',
      },
    ],
    summary: { item_count: 2, total_final_price: 2_782_225 },
  };
}

export const BOQ_PREVIEW = {
  project_id: PROJECT_ID,
  total_items: 2,
  total_chapters: 1,
  chapters: [
    {
      chapter_id: 'eeeeeeee-eeee-4eee-8eee-eeeeeeeeeeee',
      code: '001',
      name: 'فصل اول - کابل‌کشی',
      item_count: 2,
      items: [
        {
          item_id: ITEM_ID,
          code: '001-001',
          description_fa: 'کابل تحت زمینی فشار قوی ۲۰ کیلومتر',
          unit: 'کیلوگرم',
          base_price: 1_250_000,
          quantity: 10,
          row_number: 3,
        },
        {
          item_id: ITEM_ID_2,
          code: '001-002',
          description_fa: 'کابل فولادی روکش دار',
          unit: 'متر',
          base_price: 875_400,
          quantity: 25,
          row_number: 4,
        },
      ],
    },
  ],
  warnings: [],
};
