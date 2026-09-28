"""
BoQ Validators
"""
import re
from typing import Optional, List
from dataclasses import dataclass
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ValidationResult:
    is_valid: bool
    errors: List[str]
    warnings: List[str]


class BoQValidator:
    """Validator for BoQ data"""
    
    # Valid units in Iranian construction
    VALID_UNITS = {
        "متر", "م", "متر طول",
        "متر مربع", "م2", "م²",
        "متر مکعب", "م3", "م³",
        "کیلوگرم", "کگ", "kg",
        "تن", "ton",
        "عدد", "پارچه", "رول",
        "مجموعه", "ست",
    }
    
    # Valid Persian characters regex
    PERSIAN_CHARS = re.compile(r"[\u0600-\u06FF\u0750-\u077F\u08A0-\u08FF\uFB50-\uFDFF\uFE70-\uFEFF]")
    
    def __init__(self):
        self.logger = logger
    
    def validate_item_code(self, code: str) -> ValidationResult:
        """Validate BoQ item code"""
        errors = []
        warnings = []
        
        if not code or not code.strip():
            errors.append("Item code is required")
            return ValidationResult(False, errors, warnings)
        
        code = code.strip()
        
        # Check if starts with 3 digits (chapter code)
        if not re.match(r"^\d{3}", code):
            warnings.append(f"Item code '{code}' does not start with 3-digit chapter code")
        
        # Check length
        if len(code) > 50:
            errors.append(f"Item code '{code}' exceeds maximum length of 50 characters")
        
        return ValidationResult(len(errors) == 0, errors, warnings)
    
    def validate_persian_text(self, text: str, field_name: str = "text") -> ValidationResult:
        """Validate Persian text"""
        errors = []
        warnings = []
        
        if not text or not text.strip():
            warnings.append(f"{field_name} is empty")
            return ValidationResult(True, errors, warnings)
        
        # Check for Persian characters
        if not self.PERSIAN_CHARS.search(text):
            warnings.append(f"{field_name} may not contain Persian text")
        
        # Check for encoding issues
        try:
            text.encode('utf-8').decode('utf-8')
        except UnicodeError:
            errors.append(f"{field_name} has encoding issues")
        
        return ValidationResult(len(errors) == 0, errors, warnings)
    
    def validate_unit(self, unit: str) -> ValidationResult:
        """Validate unit of measurement"""
        errors = []
        warnings = []
        
        if not unit or not unit.strip():
            errors.append("Unit is required")
            return ValidationResult(False, errors, warnings)
        
        unit = unit.strip()
        
        if unit not in self.VALID_UNITS:
            warnings.append(f"Unit '{unit}' is not a standard unit. Valid units: {', '.join(sorted(self.VALID_UNITS))}")
        
        return ValidationResult(len(errors) == 0, errors, warnings)
    
    def validate_price(self, price: float) -> ValidationResult:
        """Validate base price"""
        errors = []
        warnings = []
        
        if price < 0:
            errors.append("Price cannot be negative")
        elif price == 0:
            warnings.append("Price is zero")
        elif price > 1_000_000_000_000:  # 1 trillion IRR
            warnings.append(f"Price {price:,.0f} IRR seems unusually high")
        
        return ValidationResult(len(errors) == 0, errors, warnings)
    
    def validate_quantity(self, quantity: float) -> ValidationResult:
        """Validate quantity"""
        errors = []
        warnings = []
        
        if quantity <= 0:
            errors.append("Quantity must be positive")
        elif quantity > 1_000_000:
            warnings.append(f"Quantity {quantity:,.0f} seems unusually high")
        
        return ValidationResult(len(errors) == 0, errors, warnings)
    
    def validate_item(self, item_data: dict) -> ValidationResult:
        """Validate complete BoQ item"""
        all_errors = []
        all_warnings = []
        
        # Validate code
        result = self.validate_item_code(item_data.get("code", ""))
        all_errors.extend(result.errors)
        all_warnings.extend(result.warnings)
        
        # Validate description
        result = self.validate_persian_text(item_data.get("description_fa", ""), "Description")
        all_errors.extend(result.errors)
        all_warnings.extend(result.warnings)
        
        # Validate unit
        result = self.validate_unit(item_data.get("unit", ""))
        all_errors.extend(result.errors)
        all_warnings.extend(result.warnings)
        
        # Validate price
        result = self.validate_price(item_data.get("base_price", 0))
        all_errors.extend(result.errors)
        all_warnings.extend(result.warnings)
        
        # Validate quantity
        result = self.validate_quantity(item_data.get("quantity", 1))
        all_errors.extend(result.errors)
        all_warnings.extend(result.warnings)
        
        return ValidationResult(len(all_errors) == 0, all_errors, all_warnings)


def validate_boq_item(item_data: dict) -> ValidationResult:
    """Convenience function to validate BoQ item"""
    validator = BoQValidator()
    return validator.validate_item(item_data)