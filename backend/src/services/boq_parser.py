"""
BoQ Parser Service
"""
import re
import openpyxl
import pandas as pd
from typing import List, Optional, Dict, Any
from uuid import UUID
from dataclasses import dataclass
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ParsedBoQItem:
    code: str
    description_fa: str
    description_en: Optional[str]
    unit: str
    base_price: float
    quantity: float
    row_number: int
    chapter_code: str


@dataclass
class ParsedBoQ:
    project_name: str
    items: List[ParsedBoQItem]
    warnings: List[str]


class BoQParser:
    """Service for parsing BoQ Excel files"""
    
    # Persian keywords for header detection
    HEADER_KEYWORDS = [
        "کد", "code", "شماره", "ردیف",
        "شرح", "توضیحات", "description",
        "واحد", "unit", "واحد اندازه گیری",
        "مبلغ", "قیمت", "price", "قیمت واحد",
        "تعداد", "quantity", "مقدار",
        "فصل", "chapter", "گروه",
    ]
    
    def __init__(self):
        self.logger = logger
    
    def parse(self, file_path: str) -> ParsedBoQ:
        """
        Parse BoQ Excel file and extract items with chapters
        """
        self.logger.info("parsing_boq_file", file_path=file_path)
        
        try:
            # Load workbook with openpyxl to preserve formatting
            wb = openpyxl.load_workbook(file_path, data_only=True)
            ws = wb.active
            
            # Find header row
            header_row = self._find_header_row(ws)
            if not header_row:
                raise ValueError("Could not find header row in Excel file")
            
            # Parse column indices
            col_indices = self._parse_header(ws, header_row)
            
            # Parse data rows
            items = []
            warnings = []
            
            for row_num in range(header_row + 1, ws.max_row + 1):
                try:
                    item = self._parse_row(ws, row_num, col_indices)
                    if item:
                        items.append(item)
                except Exception as e:
                    warnings.append(f"Row {row_num}: {str(e)}")
                    self.logger.warning("row_parse_error", row=row_num, error=str(e))
            
            # Determine project name from first sheet or filename
            project_name = wb.sheetnames[0] if wb.sheetnames else "Imported BoQ"
            
            self.logger.info("boq_parsed", 
                item_count=len(items), 
                warning_count=len(warnings))
            
            return ParsedBoQ(
                project_name=project_name,
                items=items,
                warnings=warnings
            )
            
        except Exception as e:
            self.logger.error("boq_parse_failed", error=str(e))
            raise
    
    @staticmethod
    def _extract_chapter_code(item_code: str) -> str:
        """
        Derive a chapter code from an item code.

        Iranian BoQ item codes are ``<chapter><item>``; the chapter is the
        leading three digits. Anything that does not start with three digits
        falls back to ``"000"`` rather than raising, so one malformed row does
        not abort a whole workbook.
        """
        match = re.match(r"^(\d{3})", str(item_code or "").strip())
        return match.group(1) if match else "000"
    
    def _find_header_row(self, ws) -> Optional[int]:
        """Find the header row by looking for Persian keywords"""
        for row_num in range(1, min(20, ws.max_row + 1)):
            row_text = " ".join([
                str(cell.value or "") for cell in ws[row_num]
            ]).lower()
            
            if any(keyword in row_text for keyword in self.HEADER_KEYWORDS):
                return row_num
        return None
    
    def _parse_header(self, ws, header_row: int) -> Dict[str, int]:
        """Parse header row to find column indices"""
        col_indices = {}
        
        for col_num, cell in enumerate(ws[header_row], 1):
            value = str(cell.value or "").lower().strip()
            
            # Map columns
            if any(k in value for k in ["کد", "code", "شماره", "ردیف"]):
                col_indices["code"] = col_num
            elif any(k in value for k in ["شرح", "توضیحات", "description"]):
                col_indices["description"] = col_num
            elif any(k in value for k in ["واحد", "unit"]):
                col_indices["unit"] = col_num
            elif any(k in value for k in ["مبلغ", "قیمت", "price"]):
                col_indices["price"] = col_num
            elif any(k in value for k in ["تعداد", "quantity", "مقدار"]):
                col_indices["quantity"] = col_num
            elif any(k in value for k in ["فصل", "chapter", "گروه"]):
                col_indices["chapter"] = col_num
        
        return col_indices
    
    def _parse_row(self, ws, row_num: int, col_indices: Dict[str, int]) -> Optional[ParsedBoQItem]:
        """Parse a single data row"""
        # Get code (required)
        code_cell = ws.cell(row=row_num, column=col_indices.get("code", 1))
        code = str(code_cell.value or "").strip()
        if not code:
            return None
        
        # Extract chapter code (first 3 digits)
        chapter_code = self._extract_chapter_code(code)
        
        # Get description
        desc = ""
        if "description" in col_indices:
            desc_cell = ws.cell(row=row_num, column=col_indices["description"])
            desc = str(desc_cell.value or "").strip()
        
        # Get unit
        unit = "متر"
        if "unit" in col_indices:
            unit_cell = ws.cell(row=row_num, column=col_indices["unit"])
            unit = str(unit_cell.value or "متر").strip()
        
        # Get price
        price = 0.0
        if "price" in col_indices:
            price_cell = ws.cell(row=row_num, column=col_indices["price"])
            try:
                price = float(price_cell.value or 0)
            except (ValueError, TypeError):
                price = 0.0
        
        # Get quantity
        qty = 1.0
        if "quantity" in col_indices:
            qty_cell = ws.cell(row=row_num, column=col_indices["quantity"])
            try:
                qty = float(qty_cell.value or 1)
            except (ValueError, TypeError):
                qty = 1.0
        
        return ParsedBoQItem(
            code=code,
            description_fa=desc,
            description_en=None,
            unit=unit,
            base_price=price,
            quantity=qty,
            row_number=row_num,
            chapter_code=chapter_code
        )


def parse_boq_excel(file_path: str) -> ParsedBoQ:
    """Convenience function to parse BoQ Excel"""
    parser = BoQParser()
    return parser.parse(file_path)