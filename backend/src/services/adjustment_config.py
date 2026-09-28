"""
Adjustment Configuration Service
"""
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, Optional
from src.core.config import settings
from src.services.commercial_adjustments import CommercialAdjustmentsService
from src.services.adjustment_override import adjustment_override_service
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ProjectAdjustmentConfig:
    """Configuration for a project's commercial adjustments"""
    risk_buffer: Decimal
    payment_terms: Decimal
    profit_margin: Decimal
    
    def to_dict(self) -> Dict[str, str]:
        return {
            "risk_buffer": str(self.risk_buffer),
            "payment_terms": str(self.payment_terms),
            "profit_margin": str(self.profit_margin),
        }
    
    @classmethod
    def from_dict(cls, data: Dict[str, str]) -> "ProjectAdjustmentConfig":
        return cls(
            risk_buffer=Decimal(data.get("risk_buffer", str(settings.DEFAULT_RISK_BUFFER))),
            payment_terms=Decimal(data.get("payment_terms", str(settings.DEFAULT_PAYMENT_TERMS))),
            profit_margin=Decimal(data.get("profit_margin", str(settings.DEFAULT_PROFIT_MARGIN))),
        )
    
    @classmethod
    def defaults(cls) -> "ProjectAdjustmentConfig":
        return cls(
            risk_buffer=Decimal(str(settings.DEFAULT_RISK_BUFFER)),
            payment_terms=Decimal(str(settings.DEFAULT_PAYMENT_TERMS)),
            profit_margin=Decimal(str(settings.DEFAULT_PROFIT_MARGIN)),
        )


class AdjustmentConfigService:
    """Service for managing adjustment configurations"""
    
    def __init__(self):
        self.logger = logger
        self._project_configs: Dict[str, ProjectAdjustmentConfig] = {}
    
    def get_project_config(self, project_id: str) -> ProjectAdjustmentConfig:
        """Get configuration for a project (with defaults)"""
        if project_id in self._project_configs:
            return self._project_configs[project_id]
        return ProjectAdjustmentConfig.defaults()
    
    def set_project_config(
        self,
        project_id: str,
        risk_buffer: Optional[Decimal] = None,
        payment_terms: Optional[Decimal] = None,
        profit_margin: Optional[Decimal] = None,
    ) -> ProjectAdjustmentConfig:
        """Set configuration for a project"""
        current = self.get_project_config(project_id)
        
        config = ProjectAdjustmentConfig(
            risk_buffer=risk_buffer or current.risk_buffer,
            payment_terms=payment_terms or current.payment_terms,
            profit_margin=profit_margin or current.profit_margin,
        )
        
        # Validate
        for name, value in [
            ("risk_buffer", config.risk_buffer),
            ("payment_terms", config.payment_terms),
            ("profit_margin", config.profit_margin),
        ]:
            if value < Decimal("1.0"):
                raise ValueError(f"{name} must be >= 1.0, got {value}")
        
        self._project_configs[project_id] = config
        
        self.logger.info("project_config_updated", project_id=project_id, config=config.to_dict())
        return config
    
    def create_service_from_project(self, project_id_or_config) -> CommercialAdjustmentsService:
        """
        Build a :class:`CommercialAdjustmentsService` for a project.

        ``project_id_or_config`` is either a project id (in which case the
        stored configuration and active overrides are loaded) or a plain
        ``{"risk_buffer": ..., "payment_terms": ..., "profit_margin": ...}``
        mapping of multipliers.
        """
        if isinstance(project_id_or_config, dict):
            config = ProjectAdjustmentConfig.from_dict(project_id_or_config)
            return CommercialAdjustmentsService(
                risk_buffer=config.risk_buffer,
                payment_terms=config.payment_terms,
                profit_margin=config.profit_margin,
            )

        project_id = project_id_or_config
        config = self.get_project_config(project_id)

        service = CommercialAdjustmentsService(
            risk_buffer=config.risk_buffer,
            payment_terms=config.payment_terms,
            profit_margin=config.profit_margin,
        )

        # Apply active overrides on top of the project configuration
        for mult_type, value in self.get_effective_multipliers(project_id).items():
            if value != service.get_effective_multiplier(mult_type):
                service.overrides[mult_type] = value
                service.override_reasons[mult_type] = "Active project override"
                setattr(service, mult_type, value)

        return service
    
    def get_effective_multipliers(self, project_id: str) -> Dict[str, Decimal]:
        """Get effective multipliers for a project (config + overrides)"""
        config = self.get_project_config(project_id)
        base = config.to_dict()
        
        return adjustment_override_service.get_effective_multipliers(project_id, base)
    
    def reset_project_config(self, project_id: str) -> None:
        """Reset project config to defaults"""
        if project_id in self._project_configs:
            del self._project_configs[project_id]
            self.logger.info("project_config_reset", project_id=project_id)


# Global instance
adjustment_config_service = AdjustmentConfigService()