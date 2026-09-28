"""
Weight Fusion Service
"""
from typing import Dict, Optional
from dataclasses import dataclass
from src.core.config import settings
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class FusionResult:
    final_weights: Dict[str, float]
    fusion_method: str
    confidence: float
    llm_weights: Dict[str, float]
    ml_weights: Dict[str, float]
    ml_r2: Dict[str, float]
    llm_certainty: float


class WeightFusion:
    """Service for fusing LLM and ML weight attributions"""
    
    COMPONENTS = ["copper", "steel", "cement", "polymer", "energy", "labor", "overhead"]
    
    def __init__(self, alpha: float = None):
        """
        Initialize fusion service
        
        Args:
            alpha: Weight for ML in fusion (default from settings: 0.7)
                   Final = alpha * ML + (1-alpha) * LLM
        """
        self.alpha = alpha if alpha is not None else settings.WEIGHT_FUSION_ALPHA
        self.logger = logger
    
    def fuse(
        self,
        llm_weights: Dict[str, float],
        ml_weights: Dict[str, float],
        ml_r2: Dict[str, float],
        llm_certainty: float,
    ) -> FusionResult:
        """
        Fuse LLM and ML weights using weighted average.

        ``ml_r2`` is the regressor's per-component breakdown, whose entries
        *sum* to the overall R² because the model attributes its explanatory
        power to components in proportion to their weight. It is therefore
        summed here, not averaged: averaging a share-attributed breakdown
        divides the model's R² by the number of components, so a perfectly
        explanatory fit scoring 1.0 would contribute 0.14 to the confidence and
        the item would be presented as barely evidenced.
        """
        # Ensure all components present
        llm_w = {c: llm_weights.get(c, 0.0) for c in self.COMPONENTS}
        ml_w = {c: ml_weights.get(c, 0.0) for c in self.COMPONENTS}
        
        # Weighted average fusion
        final_weights = {}
        for c in self.COMPONENTS:
            final_weights[c] = self.alpha * ml_w[c] + (1 - self.alpha) * llm_w[c]
        
        # Normalize to sum to 1.0
        total = sum(final_weights.values())
        if total > 0:
            final_weights = {k: v / total for k, v in final_weights.items()}
        else:
            final_weights = {c: 1.0 / len(self.COMPONENTS) for c in self.COMPONENTS}
        
        # Compute confidence
        # Weighted by ML R² and LLM certainty
        ml_fit = sum(ml_r2.values()) if ml_r2 else 0.0
        # Clamped, because a caller is free to hand in a breakdown that does not
        # sum to 1.0, and an out-of-range R² would push the confidence past
        # certainty - claiming more confidence in the ML side than the model
        # asserted.
        ml_fit = min(max(ml_fit, 0.0), 1.0)
        confidence = self.alpha * ml_fit + (1 - self.alpha) * llm_certainty
        confidence = min(max(confidence, 0.0), 1.0)
        
        return FusionResult(
            final_weights=final_weights,
            fusion_method=f"weighted_average_alpha_{self.alpha}",
            confidence=confidence,
            llm_weights=llm_w,
            ml_weights=ml_w,
            ml_r2=ml_r2,
            llm_certainty=llm_certainty,
        )
    
    def fuse_bayesian(
        self,
        llm_weights: Dict[str, float],
        ml_weights: Dict[str, float],
        ml_r2: Dict[str, float],
        llm_certainty: float,
        prior_weights: Optional[Dict[str, float]] = None,
    ) -> FusionResult:
        """
        Bayesian fusion (alternative method)
        """
        # Use prior if provided, otherwise uniform
        if prior_weights is None:
            prior_weights = {c: 1.0 / len(self.COMPONENTS) for c in self.COMPONENTS}
        
        # Precision (inverse variance) based on R² and certainty
        llm_precision = llm_certainty * 10  # Scale certainty to precision
        ml_precisions = {c: max(r2 * 10, 0.1) for c, r2 in ml_r2.items()}
        
        final_weights = {}
        for c in self.COMPONENTS:
            # Bayesian update: posterior = (prior*prec_prior + llm*prec_llm + ml*prec_ml) / total_prec
            # Simplified: weighted by precisions
            total_prec = 1.0 + llm_precision + ml_precisions.get(c, 0.1)
            final_weights[c] = (
                prior_weights[c] * 1.0 +
                llm_weights.get(c, 0.0) * llm_precision +
                ml_weights.get(c, 0.0) * ml_precisions.get(c, 0.1)
            ) / total_prec
        
        # Normalize
        total = sum(final_weights.values())
        if total > 0:
            final_weights = {k: v / total for k, v in final_weights.items()}
        
        avg_ml_r2 = sum(ml_r2.values()) / len(ml_r2) if ml_r2 else 0.0
        confidence = self.alpha * avg_ml_r2 + (1 - self.alpha) * llm_certainty
        
        return FusionResult(
            final_weights=final_weights,
            fusion_method="bayesian",
            confidence=confidence,
            llm_weights={c: llm_weights.get(c, 0.0) for c in self.COMPONENTS},
            ml_weights={c: ml_weights.get(c, 0.0) for c in self.COMPONENTS},
            ml_r2=ml_r2,
            llm_certainty=llm_certainty,
        )
    
    def expert_override(
        self,
        current_weights: Dict[str, float],
        component: str,
        new_weight: float,
        reason: str,
    ) -> FusionResult:
        """
        Apply expert override to a specific component weight
        """
        if component not in self.COMPONENTS:
            raise ValueError(f"Invalid component: {component}")
        
        if not 0 <= new_weight <= 1:
            raise ValueError("Weight must be between 0 and 1")
        
        # Adjust other weights proportionally
        remaining_weight = 1.0 - new_weight
        other_components = [c for c in self.COMPONENTS if c != component]
        current_other_sum = sum(current_weights.get(c, 0.0) for c in other_components)
        
        final_weights = dict(current_weights)
        final_weights[component] = new_weight
        
        if current_other_sum > 0:
            for c in other_components:
                final_weights[c] = current_weights.get(c, 0.0) * (remaining_weight / current_other_sum)
        else:
            # Equal distribution
            equal_weight = remaining_weight / len(other_components)
            for c in other_components:
                final_weights[c] = equal_weight
        
        return FusionResult(
            final_weights=final_weights,
            fusion_method="expert_override",
            confidence=1.0,  # Expert override has max confidence
            llm_weights={},
            ml_weights={},
            ml_r2={},
            llm_certainty=1.0,
        )


def fuse_weights(
    llm_weights: Dict[str, float],
    ml_weights: Dict[str, float],
    ml_r2: Dict[str, float],
    llm_certainty: float,
    alpha: float = None,
) -> FusionResult:
    """Convenience function to fuse weights"""
    fusion = WeightFusion(alpha=alpha)
    return fusion.fuse(llm_weights, ml_weights, ml_r2, llm_certainty)