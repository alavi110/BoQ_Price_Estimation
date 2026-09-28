"""
Unit tests for Persian text handling
"""
import pytest
from src.validators.boq import BoQValidator, ValidationResult

def test_validate_persian_text_valid():
    """Test validation of valid Persian text"""
    validator = BoQValidator()
    
    result = validator.validate_persian_text("بتنی ساده", "Description")
    assert result.is_valid
    assert len(result.errors) == 0


def test_validate_persian_text_empty():
    """Test validation of empty Persian text"""
    validator = BoQValidator()
    
    result = validator.validate_persian_text("", "Description")
    assert result.is_valid
    assert len(result.warnings) == 1


def test_validate_persian_text_no_persian_chars():
    """Test validation of text without Persian characters"""
    validator = BoQValidator()
    
    result = validator.validate_persian_text("Plain Concrete", "Description")
    assert result.is_valid
    assert len(result.warnings) == 1


def test_validate_item_code_valid():
    """Test validation of valid item code"""
    validator = BoQValidator()
    
    result = validator.validate_item_code("101001")
    assert result.is_valid


def test_validate_item_code_missing_chapter():
    """Test validation of item code without chapter prefix"""
    validator = BoQValidator()
    
    result = validator.validate_item_code("ABC001")
    assert result.is_valid
    assert len(result.warnings) == 1


def test_validate_item_code_empty():
    """Test validation of empty item code"""
    validator = BoQValidator()
    
    result = validator.validate_item_code("")
    assert not result.is_valid
    assert len(result.errors) == 1


def test_validate_unit_valid():
    """Test validation of valid units"""
    validator = BoQValidator()
    
    for unit in ["متر", "متر مربع", "متر مکعب", "کیلوگرم", "تن", "عدد"]:
        result = validator.validate_unit(unit)
        assert result.is_valid


def test_validate_unit_unknown():
    """Test validation of unknown unit"""
    validator = BoQValidator()
    
    result = validator.validate_unit("custom_unit")
    assert result.is_valid
    assert len(result.warnings) == 1


def test_validate_price_valid():
    """Test validation of valid prices"""
    validator = BoQValidator()
    
    result = validator.validate_price(5000000)
    assert result.is_valid
    
    result = validator.validate_price(0)
    assert result.is_valid
    assert len(result.warnings) == 1


def test_validate_price_negative():
    """Test validation of negative price"""
    validator = BoQValidator()
    
    result = validator.validate_price(-1000)
    assert not result.is_valid
    assert len(result.errors) == 1


def test_validate_quantity_valid():
    """Test validation of valid quantities"""
    validator = BoQValidator()
    
    result = validator.validate_quantity(10)
    assert result.is_valid
    
    result = validator.validate_quantity(1.5)
    assert result.is_valid


def test_validate_quantity_zero():
    """Test validation of zero quantity"""
    validator = BoQValidator()
    
    result = validator.validate_quantity(0)
    assert not result.is_valid


def test_validate_complete_item():
    """Test validation of complete BoQ item"""
    validator = BoQValidator()
    
    item_data = {
        "code": "101001",
        "description_fa": "بتنی ساده",
        "unit": "متر مکعب",
        "base_price": 5000000,
        "quantity": 10,
    }
    
    result = validator.validate_item(item_data)
    assert result.is_valid
    assert len(result.errors) == 0