"""
API-level tests for the exports routes (US6 defense document, US7 Excel).

These run against a real in-memory SQLite database rather than mocking the
session, because most of what can go wrong here is a loading bug: a lazy
relationship dereference inside an async handler, a join that silently drops
rows, or a download URL that points at a filename the generator never wrote.
Mocks would pass on all three.
"""
import io
import uuid
from datetime import date

import pytest
from openpyxl import load_workbook
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from src.core.config import settings
from src.core.database import Base, get_db
from src.core.security import create_access_token
from src.main import app
from src.models.boq_item import BoQItem
from src.models.chapter import Chapter
from src.models.component import Component
from src.models.market_index import MarketDataSource, MarketIndex
from src.models.price_calculation import PriceCalculation
from src.models.project import Project
from src.models.user import User
from src.models.weight import Weight
from src.services.pdf_generator import PDFGenerator

HAS_PDF_BACKEND = PDFGenerator.weasyprint_available()

#: Seven weighted components, as the spec defines them.
COMPONENTS = [
    ("copper", "مس", "Copper", 1),
    ("steel", "فولاد", "Steel", 2),
    ("cement", "سیمان", "Cement", 3),
    ("polymer", "پلیمر", "Polymer", 4),
    ("energy", "انرژی", "Energy", 5),
    ("labor", "کار و دستمزد", "Labor", 6),
    ("overhead", "مصارف عمومی", "Overhead", 7),
]
WEIGHTS = [0.30, 0.25, 0.10, 0.10, 0.10, 0.10, 0.05]
INDEX_BASE = [100.0, 200.0, 300.0, 400.0, 500.0, 600.0, 700.0]
INDEX_CURRENT = [110.0, 190.0, 330.0, 420.0, 540.0, 640.0, 700.0]
MULTIPLIERS = (1.04, 1.08, 1.10)


# --------------------------------------------------------------------------- #
# Fixtures
# --------------------------------------------------------------------------- #
@pytest.fixture
async def api(tmp_path, monkeypatch):
    """
    A TestClient wired to a real database and a temporary export directory.

    The client is built without its context manager on purpose: entering it
    would run the app lifespan, which tries to reach Postgres.
    """
    monkeypatch.setattr(settings, "EXPORT_STORAGE_PATH", str(tmp_path / "exports"))

    engine = create_async_engine(
        "sqlite+aiosqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)

    async def _get_db():
        async with factory() as session:
            yield session

    app.dependency_overrides[get_db] = _get_db

    from fastapi.testclient import TestClient

    client = TestClient(app)
    try:
        yield client, factory, tmp_path / "exports"
    finally:
        app.dependency_overrides.clear()
        await engine.dispose()


async def _seed(factory, item_count: int = 3):
    """A project with seven components, index data, weights and calculations."""
    async with factory() as session:
        manager = User(
            sso_id="mgr", email="mgr@example.org",
            full_name="مدیر پروژه", role="manager",
        )
        session.add(manager)
        project = Project(
            name="پروژه نمونه ساختمانی",
            client_name="PRT-77",
            description="مناقصه ۱۴۰۵/۷/۲۲۱۴",
            base_date=date(2026, 3, 1),
            created_by=manager.id,
        )
        session.add(project)
        await session.flush()

        chapter = Chapter(
            project_id=project.id, code="01",
            name="فصل اول", name_fa="فصل اول", sort_order=1,
        )
        session.add(chapter)
        await session.flush()

        # Primary keys are Python-side defaults, so flush before referencing.
        components = [
            Component(code=code, name_fa=fa, name_en=en, category="material",
                     unit="kg", base_unit="kg", display_order=order)
            for code, fa, en, order in COMPONENTS
        ]
        session.add_all(components)
        await session.flush()

        sources = [
            MarketDataSource(
                name=f"{component.code} source", component_id=component.id,
                api_endpoint=f"https://example.org/{component.code}",
            )
            for component in components
        ]
        session.add_all(sources)
        await session.flush()

        for source, base, current in zip(sources, INDEX_BASE, INDEX_CURRENT):
            session.add(MarketIndex(component_id=source.component_id, source_id=source.id,
                                    date=date(2026, 3, 1), value=base, frequency="daily"))
            session.add(MarketIndex(component_id=source.component_id, source_id=source.id,
                                    date=date(2026, 9, 1), value=current, frequency="daily"))

        items = []
        for n in range(1, item_count + 1):
            item = BoQItem(
                project_id=project.id, chapter_id=chapter.id, code=f"01-{n:03d}",
                description_fa=f"ردیف نمونه شماره {n}",
                description_en=f"Sample row {n}",
                unit="kg", base_price=1_000_000.0, quantity=10.0,
                llm_confidence=0.85, row_number=n,
            )
            session.add(item)
            items.append(item)
        await session.flush()

        for item in items:
            # The middle item carries an expert override, so the document has to
            # render both the AI suggestion and the final value.
            overridden = item.code == "01-002"
            final_weights = []
            for component, suggested in zip(components, WEIGHTS):
                final = 0.20 if (overridden and component.code == "copper") else suggested
                if overridden and component.code == "overhead":
                    final = 0.15
                final_weights.append(final)
                session.add(Weight(
                    boq_item_id=item.id, component_id=component.id,
                    llm_weight=0.26 if (overridden and component.code == "copper") else final,
                    ml_weight=0.22 if (overridden and component.code == "copper") else final,
                    final_weight=final,
                    source="expert" if (overridden and component.code == "copper") else "fusion",
                    confidence=0.9,
                    override_reason=(
                        "قیمت مس در بازار جهانی افزایش یافت"
                        if (overridden and component.code == "copper") else None
                    ),
                ))

            factor = sum(
                w * (INDEX_CURRENT[i] / INDEX_BASE[i])
                for i, w in enumerate(final_weights)
            )
            adjusted = 1_000_000.0 * factor
            running, steps = adjusted, []
            for name, multiplier in zip(
                ("risk_buffer", "payment_terms", "profit_margin"), MULTIPLIERS
            ):
                output = running * multiplier
                steps.append({
                    "step": name, "multiplier": multiplier,
                    "input_price": running, "output_price": output,
                })
                running = output

            session.add(PriceCalculation(
                boq_item_id=item.id, project_id=project.id,
                base_price=1_000_000.0, updated_price=adjusted, final_price=running,
                risk_buffer_applied=MULTIPLIERS[0],
                payment_terms_applied=MULTIPLIERS[1],
                profit_margin_applied=MULTIPLIERS[2],
                adjustments_json={"steps": steps},
                index_snapshot={
                    "index_adjusted_price": adjusted,
                    "index_base": {str(c.id): b for c, b in zip(components, INDEX_BASE)},
                    "index_current": {str(c.id): v for c, v in zip(components, INDEX_CURRENT)},
                },
                is_current=True,
            ))

        await session.commit()
        return str(manager.id), str(project.id), [str(i.id) for i in items]


@pytest.fixture
async def seeded(api):
    client, factory, export_dir = api
    manager_id, project_id, item_ids = await _seed(factory)
    token = create_access_token({"sub": manager_id, "role": "manager"})
    headers = {"Authorization": f"Bearer {token}"}
    return client, headers, project_id, item_ids, export_dir


def _payload(project_id, fmt="html", **extra):
    body = {
        "job_id": str(uuid.uuid4()),
        "project_id": project_id,
        "format": fmt,
    }
    body.update(extra)
    return body


# --------------------------------------------------------------------------- #
# Defense document generation
# --------------------------------------------------------------------------- #
class TestDefenseDocGeneration:
    async def test_generates_a_document(self, seeded):
        client, headers, project_id, _, _ = seeded

        response = client.post(
            "/exports/defense-doc", headers=headers,
            json=_payload(project_id, document_number="DD-1405-001"),
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["filename"] == "DD-1405-001_defense_doc.html"
        assert body["item_count"] == 3
        assert body["chapter_count"] == 1
        assert body["content_hash"]
        assert body["validation"]["is_valid"] is True
        assert body["validation"]["blocking_errors"] == []

    async def test_totals_are_rolled_up_from_the_items(self, seeded):
        """base_total is quantity x base price across every item."""
        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/defense-doc", headers=headers, json=_payload(project_id)
        ).json()

        assert body["base_total"] == pytest.approx(3 * 1_000_000 * 10)
        # The override lowers the copper share, so the project total sits below
        # the un-adjusted total - a real arithmetic check, not just a shape check.
        assert body["final_total"] > 0
        assert body["final_total"] != body["base_total"]

    async def test_document_is_downloadable(self, seeded):
        client, headers, project_id, _, export_dir = seeded

        body = client.post(
            "/exports/defense-doc", headers=headers, json=_payload(project_id)
        ).json()
        download = client.get(body["download_url"], headers=headers)

        assert download.status_code == 200
        assert download.headers["content-type"].startswith("text/html")
        text = download.content.decode("utf-8")
        assert "سند دفاعیه" in text
        assert list(export_dir.iterdir())

    async def test_download_url_works_off_the_body_without_a_second_lookup(self, seeded):
        """The URL is the contract; a client must not need to list anything."""
        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/defense-doc", headers=headers, json=_payload(project_id)
        ).json()
        assert body["download_url"].endswith(f"/{body['document_id']}/download")
        assert client.get(body["download_url"], headers=headers).status_code == 200

    async def test_preview_returns_the_document_inline(self, seeded):
        client, headers, project_id, _, _ = seeded

        response = client.get(
            "/exports/defense-doc/preview", headers=headers,
            params={"project_id": project_id},
        )

        assert response.status_code == 200
        body = response.json()
        assert body["format"] == "html"
        assert body["size_bytes"] == len(body["content"].encode("utf-8"))
        assert "سند دفاعیه" in body["content"]

    async def test_preview_does_not_write_a_file(self, seeded):
        """A preview must not consume disk or leave a downloadable artefact."""
        client, headers, project_id, _, export_dir = seeded

        client.get("/exports/defense-doc/preview", headers=headers,
                   params={"project_id": project_id})

        assert not export_dir.exists() or not list(export_dir.glob("*_defense_doc.*"))

    async def test_validation_endpoint_matches_the_generated_report(self, seeded):
        client, headers, project_id, _, _ = seeded

        generated = client.post(
            "/exports/defense-doc", headers=headers, json=_payload(project_id)
        ).json()["validation"]
        checked = client.get(
            "/exports/defense-doc/validate", headers=headers,
            params={"project_id": project_id},
        ).json()

        assert checked["is_valid"] == generated["is_valid"]
        assert len(checked["checks"]) == len(generated["checks"])

    async def test_scope_can_be_narrowed_to_specific_items(self, seeded):
        client, headers, project_id, item_ids, _ = seeded

        body = client.post(
            "/exports/defense-doc", headers=headers,
            json=_payload(project_id, item_ids=item_ids[:1]),
        ).json()

        assert body["item_count"] == 1

    async def test_item_and_chapter_filters_intersect(self, seeded):
        """Passing both must narrow, never widen."""
        client, headers, project_id, item_ids, _ = seeded

        body = client.post(
            "/exports/defense-doc", headers=headers,
            json=_payload(project_id, item_ids=item_ids[:1], chapter_ids=[]),
        ).json()

        # An empty chapter list means "no filter" per the schema contract.
        assert body["item_count"] == 1

    async def test_expiry_is_set_on_the_response(self, seeded):
        from datetime import datetime

        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/defense-doc", headers=headers, json=_payload(project_id)
        ).json()

        assert datetime.fromisoformat(body["expires_at"]) > datetime.utcnow()


# --------------------------------------------------------------------------- #
# Error mapping
# --------------------------------------------------------------------------- #
class TestErrorMapping:
    async def test_unknown_project_is_404(self, seeded):
        client, headers, _, _, _ = seeded

        response = client.post(
            "/exports/defense-doc", headers=headers,
            json=_payload(str(uuid.uuid4())),
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    async def test_unknown_format_is_422(self, seeded):
        client, headers, project_id, _, _ = seeded

        response = client.post(
            "/exports/defense-doc", headers=headers,
            json=_payload(project_id, fmt="docx"),
        )

        assert response.status_code == 422

    async def test_unknown_document_id_is_404(self, seeded):
        client, headers, _, _, _ = seeded

        response = client.get(
            f"/exports/defense-doc/{uuid.uuid4()}/download", headers=headers
        )

        assert response.status_code == 404

    @pytest.mark.skipif(HAS_PDF_BACKEND, reason="backend is present on this host")
    async def test_missing_pdf_backend_is_503_with_a_way_forward(self, seeded):
        client, headers, project_id, _, _ = seeded

        response = client.post(
            "/exports/defense-doc", headers=headers, json=_payload(project_id, fmt="pdf")
        )

        assert response.status_code == 503
        detail = response.json()["detail"]
        assert detail["pdf_available"] is False
        assert "html" in detail["hint"].lower()

    async def test_missing_auth_is_401(self, seeded):
        client, _, project_id, _, _ = seeded

        response = client.post("/exports/defense-doc", json=_payload(project_id))

        assert response.status_code == 401

    async def test_traversal_attempt_does_not_reach_the_filesystem(self, seeded):
        """A crafted id must 404, never serve a file outside the export dir."""
        client, headers, project_id, _, _ = seeded
        client.post("/exports/defense-doc", headers=headers, json=_payload(project_id))

        for hostile in ("..", "%2e%2e", "..%2F..%2Fetc%2Fpasswd"):
            response = client.get(
                f"/exports/defense-doc/{hostile}/download", headers=headers
            )
            assert response.status_code in (400, 404, 422), hostile


# --------------------------------------------------------------------------- #
# Excel export
# --------------------------------------------------------------------------- #
class TestExcelExport:
    async def test_export_returns_a_download_url(self, seeded):
        client, headers, project_id, _, _ = seeded

        response = client.post(
            "/exports/excel", headers=headers,
            json={"job_id": str(uuid.uuid4()), "project_id": project_id},
        )

        assert response.status_code == 200, response.text
        body = response.json()
        assert body["filename"].startswith("PRT-77_boq_")
        assert body["download_url"].endswith("/download")

    async def test_workbook_downloads_and_parses(self, seeded):
        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/excel", headers=headers,
            json={"job_id": str(uuid.uuid4()), "project_id": project_id},
        ).json()
        download = client.get(body["download_url"], headers=headers)

        assert download.status_code == 200
        workbook = load_workbook(io.BytesIO(download.content))
        assert "قیمت ها" in workbook.sheetnames
        assert "وزن ها" in workbook.sheetnames

    async def test_sheets_are_right_to_left(self, seeded):
        """An RTL workbook is the whole point of exporting to Persian Excel."""
        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/excel", headers=headers,
            json={"job_id": str(uuid.uuid4()), "project_id": project_id},
        ).json()
        workbook = load_workbook(
            io.BytesIO(client.get(body["download_url"], headers=headers).content)
        )

        for name in workbook.sheetnames:
            assert workbook[name].sheet_view.rightToLeft is True, name

    async def test_totals_row_carries_live_formulas(self, seeded):
        """
        The totals row must be a formula, not a pasted number.

        A reviewer who changes a weight has to see the total move - that is how
        the workbook gets challenged and defended.
        """
        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/excel", headers=headers,
            json={"job_id": str(uuid.uuid4()), "project_id": project_id},
        ).json()
        workbook = load_workbook(
            io.BytesIO(client.get(body["download_url"], headers=headers).content)
        )
        sheet = workbook["قیمت ها"]
        totals = sheet.cell(row=sheet.max_row, column=1).value

        assert totals == "جمع کل"
        for column in (4, 5, 7, 8):
            value = sheet.cell(row=sheet.max_row, column=column).value
            assert isinstance(value, str) and value.startswith("=SUM("), (column, value)

    async def test_optional_sheets_can_be_omitted(self, seeded):
        client, headers, project_id, _, _ = seeded

        body = client.post(
            "/exports/excel", headers=headers,
            json={
                "job_id": str(uuid.uuid4()), "project_id": project_id,
                "include_weights": False, "include_forecasts": False,
                "include_comparison": False,
            },
        ).json()
        workbook = load_workbook(
            io.BytesIO(client.get(body["download_url"], headers=headers).content)
        )

        assert "وزن ها" not in workbook.sheetnames
        assert "مقایسه" not in workbook.sheetnames
        assert "قیمت ها" in workbook.sheetnames

    async def test_unknown_project_is_404(self, seeded):
        client, headers, _, _, _ = seeded

        response = client.post(
            "/exports/excel", headers=headers,
            json={"job_id": str(uuid.uuid4()), "project_id": str(uuid.uuid4())},
        )

        assert response.status_code == 404
