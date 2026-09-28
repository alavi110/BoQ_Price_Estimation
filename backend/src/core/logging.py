"""
Structured logging configuration
"""
import logging
import sys
from typing import Any, Dict
import structlog
from src.core.config import settings


def setup_logging() -> None:
    """Configure structured logging"""
    
    # Configure standard library logging
    logging.basicConfig(
        format="%(message)s",
        stream=sys.stdout,
        level=getattr(logging, settings.LOG_LEVEL.upper()),
    )
    
    # Configure structlog
    structlog.configure(
        processors=[
            structlog.stdlib.filter_by_level,
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.processors.JSONRenderer(),
        ],
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )


def get_logger(name: str) -> structlog.BoundLogger:
    """Get a structured logger instance"""
    return structlog.get_logger(name)


class AuditLogger:
    """Audit logger for tracking all changes"""
    
    def __init__(self):
        self.logger = get_logger("audit")
    
    def log_change(
        self,
        entity_type: str,
        entity_id: str,
        field_name: str,
        old_value: Any,
        new_value: Any,
        user_id: str,
        action: str = "update",
        request_id: str = None,
    ) -> None:
        self.logger.info(
            "field_changed",
            entity_type=entity_type,
            entity_id=entity_id,
            field_name=field_name,
            old_value=str(old_value) if old_value is not None else None,
            new_value=str(new_value) if new_value is not None else None,
            user_id=user_id,
            action=action,
            request_id=request_id,
        )
    
    def log_llm_request(
        self,
        item_id: str,
        prompt: str,
        response: str,
        model: str,
        tokens_used: int,
        user_id: str,
    ) -> None:
        self.logger.info(
            "llm_request",
            item_id=item_id,
            model=model,
            tokens_used=tokens_used,
            user_id=user_id,
            prompt_length=len(prompt),
            response_length=len(response),
        )
    
    def log_calculation(
        self,
        item_id: str,
        calculation_type: str,
        inputs: Dict[str, Any],
        result: Dict[str, Any],
        user_id: str,
    ) -> None:
        self.logger.info(
            "calculation",
            item_id=item_id,
            calculation_type=calculation_type,
            inputs=inputs,
            result=result,
            user_id=user_id,
        )


audit_logger = AuditLogger()