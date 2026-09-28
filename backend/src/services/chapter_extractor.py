"""
Chapter Extractor Service
"""
from typing import Dict, List
from dataclasses import dataclass
from src.core.logging import get_logger

logger = get_logger(__name__)


@dataclass
class ChapterInfo:
    code: str
    name: str
    name_fa: str
    item_count: int


class ChapterExtractor:
    """Service for extracting and managing chapters from BoQ items"""
    
    # Standard Iranian construction chapter names
    CHAPTER_NAMES = {
        "101": ("مقدمات و کارهای ترمز", "Preliminaries & Site Preparation"),
        "102": ("کارهای حفاری و تکمیل", "Excavation & Backfill"),
        "103": ("کارهای بتنی", "Concrete Works"),
        "104": ("کارهای آرمه", "Reinforcement Works"),
        "105": ("کارهای آجری", "Brick Works"),
        "106": ("کارهای سنگی", "Stone Works"),
        "107": ("کارهای عایق‌بندی", "Insulation Works"),
        "108": ("کارهای مصالحه", "Plastering Works"),
        "109": ("کارهای کاشی و سنگ", "Tile & Stone Works"),
        "110": ("کارهای چوبی و فلزی", "Wood & Metal Works"),
        "111": ("کارهای رنگ و روغنی", "Painting Works"),
        "112": ("کارهای تاسیسات برقی", "Electrical Installations"),
        "113": ("کارهای تاسیسات مکانیکی", "Mechanical Installations"),
        "201": ("مقدمات و کارهای ترمز", "Preliminaries & Site Preparation"),
        "202": ("کارهای حفاری و تکمیل", "Excavation & Backfill"),
        "203": ("کارهای بتنی", "Concrete Works"),
        "204": ("کارهای آرمه", "Reinforcement Works"),
        "301": ("مقدمات و کارهای ترمز", "Preliminaries & Site Preparation"),
        "302": ("کارهای کابل و سیم", "Cable & Wire Works"),
        "303": ("کارهای تجهیزات برقی", "Electrical Equipment Works"),
        "304": ("کارهای نورپردازی", "Lighting Works"),
    }
    
    def __init__(self):
        self.logger = logger
    
    def extract_chapters(self, items: List) -> Dict[str, ChapterInfo]:
        """
        Extract chapters from BoQ items based on first 3 digits of code
        """
        chapters = {}
        
        for item in items:
            chapter_code = item.chapter_code if hasattr(item, 'chapter_code') else self._extract_chapter_code(item.code)
            
            if chapter_code not in chapters:
                name_fa, name_en = self.CHAPTER_NAMES.get(chapter_code, (f"فصل {chapter_code}", f"Chapter {chapter_code}"))
                chapters[chapter_code] = ChapterInfo(
                    code=chapter_code,
                    name=name_en,
                    name_fa=name_fa,
                    item_count=0
                )
            
            chapters[chapter_code].item_count += 1
        
        self.logger.info("chapters_extracted", chapter_count=len(chapters))
        return chapters
    
    def _extract_chapter_code(self, item_code: str) -> str:
        """Extract chapter code from item code (first 3 digits)"""
        import re
        match = re.match(r"^(\d{3})", item_code)
        return match.group(1) if match else "000"
    
    def get_chapter_name(self, chapter_code: str) -> tuple[str, str]:
        """Get chapter name as ``(name_fa, name_en)``"""
        return self.CHAPTER_NAMES.get(
            chapter_code, (f"فصل {chapter_code}", f"Chapter {chapter_code}")
        )


def extract_chapters(items: List) -> Dict[str, ChapterInfo]:
    """Convenience function to extract chapters"""
    extractor = ChapterExtractor()
    return extractor.extract_chapters(items)