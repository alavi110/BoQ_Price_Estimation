"""
Error Handler Service
"""
from typing import List, Dict, Any, Optional
from dataclasses import dataclass
from enum import Enum
from src.core.logging import get_logger

logger = get_logger(__name__)


class ErrorSeverity(Enum):
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    CRITICAL = "critical"


@dataclass
class ParseError:
    row: int
    column: Optional[str]
    message: str
    severity: ErrorSeverity = ErrorSeverity.ERROR
    field: Optional[str] = None


class ErrorHandler:
    """Service for handling and reporting parsing errors"""
    
    def __init__(self):
        self.errors: List[ParseError] = []
        self.logger = logger
    
    def add_error(
        self,
        row: int,
        message: str,
        column: Optional[str] = None,
        severity: ErrorSeverity = ErrorSeverity.ERROR,
        field: Optional[str] = None,
    ) -> None:
        """Add a parsing error"""
        error = ParseError(
            row=row,
            column=column,
            message=message,
            severity=severity,
            field=field,
        )
        self.errors.append(error)
        
        # Log based on severity
        log_method = getattr(self.logger, severity.value)
        log_method(
            "parse_error",
            row=row,
            column=column,
            field=field,
            message=message,
        )
    
    def add_warning(
        self,
        row: int,
        message: str,
        column: Optional[str] = None,
        field: Optional[str] = None,
    ) -> None:
        """Add a warning"""
        self.add_error(row, message, column, ErrorSeverity.WARNING, field)
    
    def has_errors(self) -> bool:
        """Check if there are any errors (not warnings)"""
        return any(e.severity in (ErrorSeverity.ERROR, ErrorSeverity.CRITICAL) for e in self.errors)
    
    def has_critical_errors(self) -> bool:
        """Check if there are critical errors"""
        return any(e.severity == ErrorSeverity.CRITICAL for e in self.errors)
    
    def get_errors(self) -> List[ParseError]:
        """Get all errors"""
        return self.errors
    
    def get_warnings(self) -> List[ParseError]:
        """Get all warnings"""
        return [e for e in self.errors if e.severity == ErrorSeverity.WARNING]
    
    def get_error_summary(self) -> Dict[str, Any]:
        """Get error summary for reporting"""
        return {
            "total": len(self.errors),
            "errors": len([e for e in self.errors if e.severity == ErrorSeverity.ERROR]),
            "warnings": len([e for e in self.errors if e.severity == ErrorSeverity.WARNING]),
            "critical": len([e for e in self.errors if e.severity == ErrorSeverity.CRITICAL]),
            "details": [
                {
                    "row": e.row,
                    "column": e.column,
                    "field": e.field,
                    "message": e.message,
                    "severity": e.severity.value,
                }
                for e in self.errors
            ],
        }
    
    def clear(self) -> None:
        """Clear all errors"""
        self.errors.clear()


def create_error_handler() -> ErrorHandler:
    """Factory function to create error handler"""
    return ErrorHandler()