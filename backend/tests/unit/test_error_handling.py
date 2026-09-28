"""
Unit tests for Error Handler
"""
import pytest
from src.services.error_handler import ErrorHandler, ParseError, ErrorSeverity

def test_add_error():
    """Test adding errors"""
    handler = ErrorHandler()
    
    handler.add_error(1, "Invalid price", column="price", severity=ErrorSeverity.ERROR)
    handler.add_error(2, "Missing description", column="description", severity=ErrorSeverity.WARNING)
    
    assert len(handler.errors) == 2
    assert handler.errors[0].row == 1
    assert handler.errors[0].severity == ErrorSeverity.ERROR
    assert handler.errors[1].severity == ErrorSeverity.WARNING


def test_has_errors():
    """Test checking for errors"""
    handler = ErrorHandler()
    
    assert not handler.has_errors()
    
    handler.add_error(1, "Warning only", severity=ErrorSeverity.WARNING)
    assert not handler.has_errors()
    
    handler.add_error(2, "Error", severity=ErrorSeverity.ERROR)
    assert handler.has_errors()


def test_has_critical_errors():
    """Test checking for critical errors"""
    handler = ErrorHandler()
    
    assert not handler.has_critical_errors()
    
    handler.add_error(1, "Critical error", severity=ErrorSeverity.CRITICAL)
    assert handler.has_critical_errors()


def test_get_error_summary():
    """Test getting error summary"""
    handler = ErrorHandler()
    
    handler.add_error(1, "Error 1", column="price", severity=ErrorSeverity.ERROR)
    handler.add_error(2, "Warning 1", column="description", severity=ErrorSeverity.WARNING)
    handler.add_error(3, "Critical 1", column="code", severity=ErrorSeverity.CRITICAL)
    
    summary = handler.get_error_summary()
    
    assert summary["total"] == 3
    assert summary["errors"] == 1
    assert summary["warnings"] == 1
    assert summary["critical"] == 1
    assert len(summary["details"]) == 3


def test_clear_errors():
    """Test clearing errors"""
    handler = ErrorHandler()
    
    handler.add_error(1, "Error", severity=ErrorSeverity.ERROR)
    assert handler.has_errors()
    
    handler.clear()
    assert not handler.has_errors()
    assert len(handler.errors) == 0