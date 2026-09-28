"""
Confidence Scorer for Fused Weights
"""
from typing import Dict
from dataclasses import dataclass
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ConfidenceScore:
    overall: float
    component_scores: Dict[str, float]
    factors: Dict[str, float]


class ConfidenceScorer:
    """Service for computing confidence scores for fused weights"""
    
    def __init__(self):
        self.logger = logger
    
    def compute_confidence(
        self,
        llm_weights: Dict[str, float],
        ml_weights: Dict[str, float],
        ml_r2: Dict[str, float],
        llm_certainty: float,
        fusion_alpha: float,
        weight_variance: Dict[str, float] = None,
    ) -> ConfidenceScore:
        """
        Compute confidence score for fused weights
        
        Factors:
        - ML model R² (predictive power)
        - LLM certainty (semantic confidence)
        - Agreement between LLM and ML (low variance = high confidence)
        - Fusion alpha (trust in ML vs LLM)
        """
        components = list(llm_weights.keys())
        
        # Factor 1: ML R² score
        ml_r2_score = sum(ml_r2.values()) / len(ml_r2) if ml_r2 else 0.0
        
        # Factor 2: LLM certainty
        llm_certainty_score = llm_certainty
        
        # Factor 3: Agreement between LLM and ML
        agreement_scores = {}
        for c in components:
            diff = abs(llm_weights.get(c, 0.0) - ml_weights.get(c, 0.0))
            agreement_scores[c] = max(0.0, 1.0 - diff * 2)  # 1.0 if identical, 0.0 if opposite
        
        avg_agreement = sum(agreement_scores.values()) / len(agreement_scores)
        
        # Factor 4: Weight variance (if available)
        variance_score = 1.0
        if weight_variance:
            avg_var = sum(weight_variance.values()) / len(weight_variance)
            variance_score = max(0.0, 1.0 - avg_var * 10)
        
        # Overall confidence (weighted)
        # Using fusion_alpha to weight ML vs LLM factors
        overall = (
            fusion_alpha * ml_r2_score +
            (1 - fusion_alpha) * llm_certainty_score +
            0.2 * avg_agreement +
            0.1 * variance_score
        ) / (fusion_alpha + (1 - fusion_alpha) + 0.2 + 0.1)
        
        overall = min(max(overall, 0.0), 1.0)
        
        # Per-component confidence
        component_scores = {}
        for c in components:
            comp_conf = (
                fusion_alpha * ml_r2.get(c, 0.0) +
                (1 - fusion_alpha) * llm_certainty +
                0.2 * agreement_scores[c]
            ) / (fusion_alpha + (1 - fusion_alpha) + 0.2)
            component_scores[c] = min(max(comp_conf, 0.0), 1.0)
        
        return ConfidenceScore(
            overall=overall,
            component_scores=component_scores,
            factors={
                "ml_r2": ml_r2_score,
                "llm_certainty": llm_certainty_score,
                "agreement": avg_agreement,
                "variance": variance_score,
            }
        )
    
    #: Bucket boundaries, highest first. Four bands rather than three, because
    #: this is a *numeric* score and "below 0.4" is not the same claim as
    #: "0.0-0.1"; the count-based chapter label in ``forecast_service`` uses
    #: three because item count genuinely has only three useful bands.
    LEVEL_BANDS = ((0.8, "high"), (0.6, "medium"), (0.4, "low"))

    def get_confidence_level(self, score: float) -> str:
        """
        Get a human-readable confidence level.

        Lowercase, to match ``forecast_service.CONFIDENCE_BY_COUNT`` and
        ``confidence_calculator``. Those two and this one all feed the same
        defence document, and a template or API consumer switching on the label
        would silently match nothing against a capitalised ``"High"``.

        Anything under 0.4 is ``"very_low"``.
        """
        for threshold, label in self.LEVEL_BANDS:
            if score >= threshold:
                return label
        return "very_low"