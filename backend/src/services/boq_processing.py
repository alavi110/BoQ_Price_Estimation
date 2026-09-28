"""
BoQ Processing Service
"""
from typing import Optional, List
from uuid import UUID
from dataclasses import dataclass
from src.core.logging import get_logger
from src.services.boq_parser import BoQParser, ParsedBoQ, ParsedBoQItem
from src.services.chapter_extractor import ChapterExtractor, ChapterInfo
from src.models.project import Project
from src.models.chapter import Chapter
from src.models.boq_item import BoQItem
from src.models.component import Component
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select

logger = get_logger(__name__)


@dataclass
class BoQProcessingResult:
    project_id: UUID
    chapters_created: int
    items_created: int
    warnings: List[str]


class BoQProcessingService:
    """Service for processing BoQ uploads end-to-end"""
    
    def __init__(self):
        self.parser = BoQParser()
        self.chapter_extractor = ChapterExtractor()
        self.logger = logger
    
    async def process_boq_upload(
        self,
        db: AsyncSession,
        file_path: str,
        project_id: Optional[UUID] = None,
        create_new_project: bool = False,
        project_name: Optional[str] = None,
        user_id: Optional[UUID] = None,
    ) -> BoQProcessingResult:
        """
        Process a BoQ upload: parse file, create project/chapters/items
        """
        self.logger.info("processing_boq_upload", 
            file_path=file_path, 
            project_id=str(project_id) if project_id else None)
        
        # Parse the Excel file
        parsed_boq = self.parser.parse(file_path)
        
        # Create or get project
        if create_new_project or not project_id:
            project = Project(
                name=project_name or parsed_boq.project_name,
                base_date=None,  # Will be set by user
                created_by=user_id,
                status="draft",
            )
            db.add(project)
            await db.flush()
            project_id = project.id
        else:
            project = await db.get(Project, project_id)
            if not project:
                raise ValueError(f"Project {project_id} not found")
        
        # Extract chapters
        chapters_data = self.chapter_extractor.extract_chapters(parsed_boq.items)
        
        # Create chapters
        chapter_map = {}
        for chapter_code, chapter_info in chapters_data.items():
            chapter = Chapter(
                project_id=project_id,
                code=chapter_info.code,
                name=chapter_info.name,
                name_fa=chapter_info.name_fa,
                sort_order=int(chapter_code) if chapter_code.isdigit() else 0,
            )
            db.add(chapter)
            await db.flush()
            chapter_map[chapter_code] = chapter.id
        
        # Get default components for weight initialization
        components_result = await db.execute(select(Component).where(Component.is_active == True))
        components = components_result.scalars().all()
        component_ids = [c.id for c in components]
        
        # Create BoQ items
        items_created = 0
        for item in parsed_boq.items:
            chapter_id = chapter_map.get(item.chapter_code)
            if not chapter_id:
                continue
            
            boq_item = BoQItem(
                project_id=project_id,
                chapter_id=chapter_id,
                code=item.code,
                description_fa=item.description_fa,
                description_en=item.description_en,
                unit=item.unit,
                base_price=item.base_price,
                quantity=item.quantity,
                row_number=item.row_number,
                status="pending",
            )
            db.add(boq_item)
            await db.flush()
            
            # Create empty weight records for each component
            for comp_id in component_ids:
                from src.models.weight import Weight
                weight = Weight(
                    boq_item_id=boq_item.id,
                    component_id=comp_id,
                    final_weight=0.0,
                    source="llm",
                )
                db.add(weight)
            
            items_created += 1
        
        await db.commit()
        
        self.logger.info("boq_processing_complete",
            project_id=str(project_id),
            chapters_created=len(chapters_data),
            items_created=items_created)
        
        return BoQProcessingResult(
            project_id=project_id,
            chapters_created=len(chapters_data),
            items_created=items_created,
            warnings=parsed_boq.warnings,
        )


async def process_boq_upload(
    db: AsyncSession,
    file_path: str,
    project_id: Optional[UUID] = None,
    create_new_project: bool = False,
    project_name: Optional[str] = None,
    user_id: Optional[UUID] = None,
) -> BoQProcessingResult:
    """Convenience function to process BoQ upload"""
    service = BoQProcessingService()
    return await service.process_boq_upload(
        db=db,
        file_path=file_path,
        project_id=project_id,
        create_new_project=create_new_project,
        project_name=project_name,
        user_id=user_id,
    )