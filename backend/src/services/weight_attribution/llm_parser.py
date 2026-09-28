"""
LLM Parser for Weight Attribution
"""
import json
from typing import Dict, List, Optional, Any
from dataclasses import dataclass

from src.core.config import settings
from src.core.logging import get_logger, audit_logger
from src.integrations.openrouter import openrouter_integration

logger = get_logger(__name__)

#: The seven canonical cost components. Declared here as well as on the
#: regressor and the fusion service, because the parser is the boundary where a
#: model's free-form JSON is turned into a weight vector - and it is the only
#: one of the three that has to decide what to do with a key it does not
#: recognise.
COMPONENTS = [
    "copper", "steel", "cement", "polymer", "energy", "labor", "overhead",
]


@dataclass
class LLMWeightResult:
    component_weights: Dict[str, float]
    category: str
    confidence: float
    reasoning: str
    tokens_used: int
    model_used: str


class LLMParser:
    """LLM-based weight attribution using OpenRouter"""

    # Few-shot examples for Persian BoQ classification. These are sent to the
    # model on every request, so they are the highest-leverage text in the
    # feature: the component mix of an unrecognised item tends to copy the mix
    # of whichever example it resembles most.
    FEW_SHOT_EXAMPLES = """
Examples:
1. Item: "کابل تحت زمینی ۳×۱۵۰ میلی‌متر مربع مسی"
   Category: "Electrical Cable"
   Components: {"copper": 0.70, "polymer": 0.20, "labor": 0.10}
   Confidence: 0.95

2. Item: "بتن آرمه ۳۵۰ کیلوگرم در سانتی‌متر مربع"
   Category: "Concrete"
   Components: {"cement": 0.30, "steel": 0.25, "energy": 0.15, "labor": 0.20, "overhead": 0.10}
   Confidence: 0.90

3. Item: "لوله پلی اتیلن ۱۱۰ میلی‌متر"
   Category: "Piping"
   Components: {"polymer": 0.60, "labor": 0.25, "energy": 0.10, "overhead": 0.05}
   Confidence: 0.88

4. Item: "عملیات حفاری مکانیکی خاک معمولی"
   Category: "Excavation"
   Components: {"energy": 0.40, "labor": 0.35, "overhead": 0.15, "steel": 0.10}
   Confidence: 0.85
"""

    SYSTEM_PROMPT = f"""You are an expert cost estimator for Iranian construction and electrical projects.
Your task is to analyze Bill of Quantities (BoQ) items and determine the weight percentage of each cost component.

The 7 cost components are:
1. copper (مس) - Copper materials
2. steel (فولاد) - Steel/Rebar materials
3. cement (سیمان) - Cement/Concrete materials
4. polymer (پلیمر) - Polymer/Plastic materials
5. energy (انرژی) - Energy/Fuel costs
6. labor (نیروی کار) - Labor costs
7. overhead (مصارف عمومی) - Overhead/Indirect costs

For each item, you must:
1. Classify the item into a construction/electrical category
2. Identify which components are relevant
3. Assign weight percentages that sum to 1.0 (100%)
4. Provide a confidence score (0-1)
5. Explain your reasoning

Respond ONLY with valid JSON in this format:
{{
    "category": "string",
    "components": {{"copper": 0.0, "steel": 0.0, "cement": 0.0, "polymer": 0.0, "energy": 0.0, "labor": 0.0, "overhead": 0.0}},
    "confidence": 0.0,
    "reasoning": "string"
}}"""

    def __init__(self, integration=None):
        """
        Args:
            integration: the OpenRouter integration to use. Injectable so the
                service can be exercised without a network, and so a test or a
                different deployment can substitute a transport.
        """
        self.logger = logger
        self.integration = (
            integration if integration is not None else openrouter_integration
        )

    @property
    def is_available(self) -> bool:
        """Whether a real LLM call is possible in this deployment."""
        return bool(getattr(self.integration, "is_configured", False))

    async def analyze_item(
        self,
        item_code: str,
        description_fa: str,
        description_en: Optional[str] = None,
        unit: str = "",
        project_context: Optional[Dict] = None,
    ) -> LLMWeightResult:
        """
        Analyze a single BoQ item and return weight attribution

        Never raises. A failed or unparseable response degrades to the
        keyword-based fallback, which reports its own low confidence so the
        fusion and the document both show that the LLM did not answer.
        """
        if not self.is_available:
            return self._fallback_analysis(description_fa)

        prompt = self._build_prompt(
            item_code, description_fa, description_en, unit, project_context
        )

        try:
            response = await self.integration.chat_completion(
                messages=[
                    {"role": "system", "content": self.SYSTEM_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                temperature=0.1,
                max_tokens=500,
                response_format={"type": "json_object"},
                model=settings.DEFAULT_LLM_MODEL,
            )
        except Exception as e:
            # Deliberately broad. The caller's only alternative is to fail the
            # whole analysis run, and a keyword-based answer plus a 0.3
            # confidence flag is more useful to a reviewer than no weights.
            self.logger.error("llm_analysis_failed", item_code=item_code, error=str(e))
            return self._fallback_analysis(description_fa)

        result = self._parse_response(response["content"], description_fa)

        # Only a *parsed* result inherits the responding model and its token
        # count. Overwriting these unconditionally would stamp the fallback's
        # "fallback" marker with the model that was asked, erasing the only
        # signal that the LLM did not answer - and booking tokens against a
        # fallback that never used them.
        if result.model_used != "fallback":
            result.tokens_used = response.get("tokens_used", 0) or 0
            result.model_used = response.get("model", settings.DEFAULT_LLM_MODEL)

        # Log for audit
        audit_logger.log_llm_request(
            item_id=item_code,
            prompt=prompt,
            response=response["content"],
            model=result.model_used,
            tokens_used=result.tokens_used,
            user_id="system",
        )

        return result

    def _build_prompt(
        self,
        item_code: str,
        description_fa: str,
        description_en: Optional[str],
        unit: str,
        project_context: Optional[Dict],
    ) -> str:
        """
        Build the user turn.

        The system prompt is passed separately as the system message and is
        deliberately *not* repeated here: inlining it doubled the input tokens
        of every call for no change in the model's instructions.
        """
        context = ""
        if project_context:
            context = f"Project context: {json.dumps(project_context, ensure_ascii=False)}\n"

        en_part = f"\nEnglish description: {description_en}" if description_en else ""

        return f"""{self.FEW_SHOT_EXAMPLES}

{context}Analyze this item:
Code: {item_code}
Description (Persian): {description_fa}{en_part}
Unit: {unit}"""

    def _parse_response(self, response: str, description_fa: str) -> LLMWeightResult:
        """
        Parse and sanitise the model's JSON reply.

        Everything the model returns is treated as untrusted, because each
        failure below has a plausible-looking wrong answer behind it rather
        than an error: a negative weight scales a price *down*, and a
        non-numeric one raises a ``TypeError`` in the middle of a sum that
        would otherwise have produced a number.
        """
        try:
            data = json.loads(response)
        except (json.JSONDecodeError, TypeError) as e:
            self.logger.error(
                "llm_response_parse_error",
                error=str(e),
                response=str(response)[:200],
            )
            return self._fallback_analysis(description_fa)

        if not isinstance(data, dict):
            self.logger.error("llm_response_not_an_object", type=type(data).__name__)
            return self._fallback_analysis(description_fa)

        components = self._sanitise_components(data.get("components"))
        if components is None:
            return self._fallback_analysis(description_fa)

        return LLMWeightResult(
            component_weights=components,
            category=str(data.get("category") or "Unknown"),
            confidence=self._sanitise_confidence(data.get("confidence")),
            reasoning=str(data.get("reasoning") or ""),
            tokens_used=0,  # filled in by the caller, which sees the usage
            model_used=settings.DEFAULT_LLM_MODEL,
        )

    def _sanitise_components(self, raw) -> Optional[Dict[str, float]]:
        """
        Coerce the model's component block into a valid weight vector.

        Returns ``None`` when nothing usable survives, which the caller treats
        as a failed parse and answers from the fallback. A *degraded but valid*
        vector is not silently returned: dropping an unusable entry and
        renormalising the rest would attribute a share the model never gave.
        """
        if not isinstance(raw, dict):
            self.logger.error("llm_components_not_an_object", type=type(raw).__name__)
            return None

        unknown = sorted(set(raw) - set(COMPONENTS))
        if unknown:
            # Dropped and logged, not rejected. The seven shares the model did
            # give are its own stated figures, so renormalising them over
            # itself gives what the model would have returned had it not invented
            # an eighth component. Rejecting the whole answer would discard a
            # usable attribution over one stray key - but the key is recorded,
            # because a component the price formula has no index for is exactly
            # the sort of thing an expert override exists to correct.
            self.logger.warning(
                "llm_components_outside_canonical_set", components=unknown
            )

        cleaned: Dict[str, float] = {code: 0.0 for code in COMPONENTS}
        for code in COMPONENTS:
            if code not in raw:
                continue
            value = raw[code]
            # bool is a subclass of int, and True would become a weight of 1.0.
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                self.logger.error(
                    "llm_component_not_numeric", component=code, value=repr(value)
                )
                return None
            if value < 0:
                # A material cannot be a negative share of a cost. The model may
                # mean an offset, but there is no way to tell, and clamping to
                # zero would quietly delete the entry.
                self.logger.error("llm_component_negative", component=code, value=value)
                return None
            cleaned[code] = float(value)

        total = sum(cleaned.values())
        if total <= 0:
            # Nothing but zeros: there is no cost structure to read.
            self.logger.error("llm_components_all_zero")
            return None

        return {code: value / total for code, value in cleaned.items()}

    @staticmethod
    def _sanitise_confidence(value) -> float:
        """
        Clamp the reported confidence into the unit interval.

        Out-of-range confidence is a claim about how much to trust the answer,
        so an unclamped 1.5 would flow into the fusion as certainty the model
        never asserted, and a negative one would subtract from the score.
        """
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return 0.5
        return float(min(max(value, 0.0), 1.0))

    def _fallback_analysis(self, description_fa: str) -> LLMWeightResult:
        """
        Keyword-based classification, used when the LLM cannot answer.

        The confidence of 0.3 is load-bearing: it is what tells the fusion to
        lean on the ML side and the document to show where the weights came
        from. A fallback that reported high confidence would be an
        indistinguishable lie.
        """
        desc = (description_fa or "").lower()

        components = {
            "copper": 0.0,
            "steel": 0.0,
            "cement": 0.0,
            "polymer": 0.0,
            "energy": 0.0,
            "labor": 0.0,
            "overhead": 0.10,
        }

        # Simple keyword matching
        if any(k in desc for k in ["مسی", "مس", "copper", "کابل", "سیم"]):
            components["copper"] = 0.60
            components["polymer"] = 0.20
            components["labor"] = 0.10
        elif any(k in desc for k in ["بتنی", "بتن", "concrete", "سیمان", "cement"]):
            components["cement"] = 0.30
            components["steel"] = 0.25
            components["energy"] = 0.15
            components["labor"] = 0.20
        elif any(k in desc for k in ["فولاد", "steel", "آرمه", "آرماتور", "rebar"]):
            components["steel"] = 0.70
            components["labor"] = 0.20
        elif any(k in desc for k in ["لوله", "pipe", "پلیمر", "polymer", "پلی اتیلن"]):
            components["polymer"] = 0.60
            components["labor"] = 0.25
            components["energy"] = 0.10
        elif any(k in desc for k in ["حفاری", "excavation", "خاک"]):
            components["energy"] = 0.40
            components["labor"] = 0.35
            components["steel"] = 0.10
        else:
            # Default distribution
            components = {
                "copper": 0.10,
                "steel": 0.20,
                "cement": 0.20,
                "polymer": 0.10,
                "energy": 0.15,
                "labor": 0.15,
                "overhead": 0.10,
            }

        # Normalize
        total = sum(components.values())
        components = {k: v / total for k, v in components.items()}

        return LLMWeightResult(
            component_weights=components,
            category="Unknown (Fallback)",
            confidence=0.3,
            reasoning="Fallback keyword-based classification",
            tokens_used=0,
            model_used="fallback",
        )


async def parse_item_weights(
    item_code: str,
    description_fa: str,
    description_en: Optional[str] = None,
    unit: str = "",
    project_context: Optional[Dict] = None,
) -> LLMWeightResult:
    """Convenience function to parse item weights"""
    parser = LLMParser()
    return await parser.analyze_item(item_code, description_fa, description_en, unit, project_context)


async def parse_item_weights(
    item_code: str,
    description_fa: str,
    description_en: Optional[str] = None,
    unit: str = "",
    project_context: Optional[Dict] = None,
) -> LLMWeightResult:
    """Convenience function to parse item weights"""
    parser = LLMParser()
    return await parser.analyze_item(item_code, description_fa, description_en, unit, project_context)