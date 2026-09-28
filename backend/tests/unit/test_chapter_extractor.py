"""
Unit tests for Chapter Extractor
"""
import pytest
from src.services.chapter_extractor import ChapterExtractor, ChapterInfo

def test_extract_chapters_from_items():
    """Test chapter extraction from BoQ items"""
    extractor = ChapterExtractor()
    
    class MockItem:
        def __init__(self, code):
            self.code = code
            self.chapter_code = code[:3] if len(code) >= 3 else "000"
    
    items = [
        MockItem("101001"),
        MockItem("101002"),
        MockItem("201001"),
        MockItem("301001"),
    ]
    
    chapters = extractor.extract_chapters(items)
    
    assert len(chapters) == 3
    assert "101" in chapters
    assert "201" in chapters
    assert "301" in chapters
    assert chapters["101"].item_count == 2
    assert chapters["201"].item_count == 1
    assert chapters["301"].item_count == 1


def test_chapter_names():
    """Test standard chapter names"""
    extractor = ChapterExtractor()
    
    name_fa, name_en = extractor.get_chapter_name("101")
    assert "مقدمات" in name_fa or "کارهای ترمز" in name_fa
    
    name_fa, name_en = extractor.get_chapter_name("999")
    assert name_fa == "فصل 999"
    assert name_en == "Chapter 999"


def test_extract_chapter_code():
    """Test chapter code extraction"""
    extractor = ChapterExtractor()
    
    assert extractor._extract_chapter_code("101001") == "101"
    assert extractor._extract_chapter_code("201") == "201"
    assert extractor._extract_chapter_code("ABC123") == "000"
    assert extractor._extract_chapter_code("") == "000"