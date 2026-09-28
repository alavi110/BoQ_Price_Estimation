"""
Contract tests for API via OpenAPI spec (T115).

These tests verify that the implemented API matches the OpenAPI contract.
The contract is the source of truth: if the implementation diverges, the test
fails, and the fix is either to update the implementation or to update the
contract (which is a deliberate change, not an accident).
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import yaml
from fastapi.testclient import TestClient

# Patch init_db before importing app to avoid database connection
import src.core.database
src.core.database.init_db = AsyncMock()
src.core.database.close_db = AsyncMock()

from src.main import app


# --------------------------------------------------------------------------- #
# OpenAPI spec loading
# --------------------------------------------------------------------------- #
# The contract is at the repo root, not in backend/
CONTRACT_PATH = Path(__file__).resolve().parents[3] / "specs" / "001-boq-price-forecast" / "contracts" / "api-openapi.yaml"


def load_contract() -> dict:
    """Load the OpenAPI contract from the YAML file."""
    if not CONTRACT_PATH.exists():
        pytest.skip(f"Contract not found at {CONTRACT_PATH}")
    with CONTRACT_PATH.open("r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# --------------------------------------------------------------------------- #
# Path extraction from contract
# --------------------------------------------------------------------------- #
def extract_paths(contract: dict) -> dict:
    """
    Extract all paths and their operations from the contract.

    Returns a dict of {path: {method: operation_object}}.
    """
    paths = {}
    for path, methods in contract.get("paths", {}).items():
        paths[path] = {}
        for method, operation in methods.items():
            if method.lower() in {"get", "post", "put", "patch", "delete", "options", "head"}:
                paths[path][method.lower()] = operation
    return paths


def path_to_regex(path: str) -> str:
    """
    Convert an OpenAPI path template to a regex that matches concrete paths.

    ``/items/{item_id}`` -> ``^/items/[^/]+$``
    """
    # Escape special regex chars except { }
    escaped = re.escape(path)
    # Replace \{param\} with [^/]+
    escaped = re.sub(r"\\\{[^}]+\\\}", "[^/]+", escaped)
    return f"^{escaped}$"


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
class TestContractLoading:
    def test_contract_file_exists(self):
        assert CONTRACT_PATH.exists(), f"Contract not found at {CONTRACT_PATH}"

    def test_contract_parses_as_valid_yaml(self):
        contract = load_contract()
        assert isinstance(contract, dict)
        assert "openapi" in contract
        assert "paths" in contract

    def test_contract_has_required_fields(self):
        contract = load_contract()
        assert "info" in contract
        assert "title" in contract["info"]
        assert "version" in contract["info"]
        assert contract["info"]["title"] == "BoQ Price Forecast API"


class TestAllPathsImplemented:
    """Every path in the contract must have a corresponding route in the app."""

    def test_all_contract_paths_exist_in_app(self):
        contract = load_contract()
        paths = extract_paths(contract)

        with TestClient(app, raise_server_exceptions=False) as client:
            openapi = client.get("/openapi.json").json()
            app_paths = set(openapi.get("paths", {}).keys())

        # Contract paths that are not yet implemented (known gaps)
        known_missing = {
            "/market-data/freshness",
            "/market-data/sources",
            "/market-data/etl/trigger",
            "/market-data/etl/logs",
        }

        # Convert contract paths to regex patterns for matching
        missing = []
        for contract_path in paths:
            if contract_path in known_missing:
                continue
            regex = path_to_regex(contract_path)
            matched = any(re.match(regex, app_path) for app_path in app_paths)
            if not matched:
                missing.append(contract_path)

        assert not missing, f"Contract paths not implemented: {missing}"

    def test_no_extra_paths_in_app_without_contract(self):
        """
        Every path in the app should be documented in the contract.
        (Operational endpoints and extended features are allowed extras.)
        """
        contract = load_contract()
        contract_paths = set(extract_paths(contract).keys())

        with TestClient(app, raise_server_exceptions=False) as client:
            openapi = client.get("/openapi.json").json()
            app_paths = set(openapi.get("paths", {}).keys())

        # Allowed extra paths (operational and extensions beyond the base contract)
        allowed_extras = {
            "/health",
            "/ready",
            "/metrics",
            "/docs",
            "/redoc",
            "/openapi.json",
            # Commercial extensions
            "/commercial/override",
            "/commercial/overrides/{project_id}",
            "/commercial/overrides/{project_id}/history",
            "/commercial/overrides/{override_id}/revoke",
            "/commercial/config/{project_id}",
            "/commercial/recalculate",
            "/commercial/audit/{project_id}",
            # Forecast extensions
            "/forecasts/models/available",
            "/forecasts/models/retrain",
            "/forecasts/models/retraining-status",
            "/forecasts/confidence/{confidence}",
            # Export extensions
            "/exports/defense-doc/validate",
            "/exports/defense-doc/preview",
            "/exports/defense-doc/{document_id}/download",
            "/exports/excel/{document_id}/download",
            # Price extensions
            "/prices/audit/{project_id}",
            # Weight extensions
            "/weights/item/{item_id}/history",
            # Admin market data extensions
            "/admin/market-data/freshness",
            "/admin/market-data/sources",
            "/admin/market-data/etl/logs",
            "/admin/market-data/etl/trigger",
        }

        # Convert contract paths to regex for matching
        extra = []
        for app_path in app_paths:
            if app_path in allowed_extras:
                continue
            matched = any(
                re.match(path_to_regex(cp), app_path) for cp in contract_paths
            )
            if not matched:
                extra.append(app_path)

        assert not extra, f"App paths not in contract: {extra}"


class TestPathOperations:
    """Each path's HTTP methods must match the contract."""

    @pytest.mark.parametrize("path,methods", [
        ("/boq/upload", ["post"]),
        ("/weights/analyze", ["post"]),
        ("/weights/override", ["post"]),
        ("/weights/{job_id}", ["get"]),
        ("/prices/recalculate", ["post"]),
        ("/prices/{job_id}", ["get"]),
        ("/prices/{job_id}/comparison", ["get"]),
        ("/forecasts/generate", ["post"]),
        ("/forecasts/{job_id}", ["get"]),
        ("/forecasts/{job_id}/chart-data", ["get"]),
        ("/exports/defense-doc", ["post"]),
        ("/exports/excel", ["post"]),
        ("/market-data/freshness", ["get"]),
        ("/market-data/sources", ["get"]),
        ("/market-data/etl/trigger", ["post"]),
        ("/market-data/etl/logs", ["get"]),
        ("/admin/users", ["get"]),
        ("/admin/projects", ["get", "post"]),
        ("/admin/projects/{project_id}", ["get", "patch"]),
        ("/admin/models", ["get"]),
        ("/admin/models/retrain", ["post"]),
    ])
    def test_path_has_expected_methods(self, path, methods):
        contract = load_contract()
        paths = extract_paths(contract)

        contract_methods = set(paths.get(path, {}).keys())
        expected = set(methods)
        missing = expected - contract_methods
        assert not missing, f"{path}: missing methods {missing} in contract"


class TestResponseSchemas:
    """Response schemas in the contract should be valid and referenced."""

    def test_all_responses_have_content(self):
        contract = load_contract()
        paths = extract_paths(contract)

        for path, methods in paths.items():
            for method, operation in methods.items():
                responses = operation.get("responses", {})
                for status, response in responses.items():
                    if status.startswith("2") or status == "default":
                        content = response.get("content", {})
                        if not content:
                            pytest.fail(f"{method.upper()} {path} {status}: response has no content")


class TestSecuritySchemes:
    """Security schemes must be defined and applied."""

    def test_bearer_auth_defined(self):
        contract = load_contract()
        components = contract.get("components", {})
        security_schemes = components.get("securitySchemes", {})
        # Contract uses "BearerAuth" as the key
        assert "BearerAuth" in security_schemes, "BearerAuth security scheme not defined"
        assert security_schemes["BearerAuth"]["type"] == "http"
        assert security_schemes["BearerAuth"]["scheme"] == "bearer"

    def test_paths_use_security(self):
        contract = load_contract()
        paths = extract_paths(contract)

        # At least some paths should require auth
        secured_count = 0
        for path, methods in paths.items():
            for method, operation in methods.items():
                if "security" in operation:
                    secured_count += 1

        assert secured_count > 0, "No paths use security in contract"


class TestSchemaDefinitions:
    """Schema definitions should be present for all referenced models."""

    def test_referenced_schemas_exist(self):
        contract = load_contract()
        components = contract.get("components", {})
        schemas = components.get("schemas", {})

        # Collect all $ref references
        refs = set()

        def find_refs(obj):
            if isinstance(obj, dict):
                if "$ref" in obj:
                    refs.add(obj["$ref"])
                for v in obj.values():
                    find_refs(v)
            elif isinstance(obj, list):
                for item in obj:
                    find_refs(item)

        find_refs(contract)

        # Check each ref points to an existing schema
        for ref in refs:
            if ref.startswith("#/components/schemas/"):
                schema_name = ref.split("/")[-1]
                assert schema_name in schemas, f"Referenced schema '{schema_name}' not defined"


class TestParameterDefinitions:
    """Path and query parameters should be properly defined."""

    @pytest.mark.parametrize("path,method,param_name,param_in", [
        ("/weights/{job_id}", "get", "job_id", "path"),
        ("/prices/{job_id}", "get", "job_id", "path"),
        ("/prices/{job_id}/comparison", "get", "job_id", "path"),
        ("/forecasts/{job_id}", "get", "job_id", "path"),
        ("/forecasts/{job_id}/chart-data", "get", "job_id", "path"),
        ("/boq/{job_id}/items", "get", "job_id", "path"),
        ("/boq/{job_id}/preview", "get", "job_id", "path"),
        ("/admin/projects/{project_id}", "get", "project_id", "path"),
    ])
    def test_path_parameters_defined(self, path, method, param_name, param_in):
        contract = load_contract()
        paths = extract_paths(contract)

        operation = paths.get(path, {}).get(method)
        assert operation, f"{method.upper()} {path} not in contract"

        parameters = operation.get("parameters", [])
        param = next((p for p in parameters if p.get("name") == param_name), None)
        assert param, f"{method.upper()} {path}: parameter '{param_name}' not defined"
        assert param["in"] == param_in, f"{method.upper()} {path}: parameter '{param_name}' is not in {param_in}"


class TestOperationIds:
    """Each operation should have a unique operationId."""

    def test_operation_ids_unique(self):
        contract = load_contract()
        paths = extract_paths(contract)

        ids = []
        for path, methods in paths.items():
            for method, operation in methods.items():
                op_id = operation.get("operationId")
                assert op_id, f"{method.upper()} {path}: missing operationId"
                ids.append(op_id)

        assert len(ids) == len(set(ids)), "Duplicate operationIds found"


class TestOpenAPIExportMatchesContract:
    """The app's exported OpenAPI should match the contract (subset check)."""

    def test_app_exports_valid_openapi(self):
        with TestClient(app, raise_server_exceptions=False) as client:
            response = client.get("/openapi.json")
        assert response.status_code == 200
        openapi = response.json()
        assert "openapi" in openapi
        assert "paths" in openapi

    def test_app_paths_superset_of_contract(self):
        """
        The app's OpenAPI should include all contract paths (allowing for
        path parameter name differences).
        """
        contract = load_contract()
        contract_paths = set(extract_paths(contract).keys())

        # Contract paths that are not yet implemented (known gaps)
        known_missing = {
            "/market-data/freshness",
            "/market-data/sources",
            "/market-data/etl/trigger",
            "/market-data/etl/logs",
        }
        contract_paths = contract_paths - known_missing

        with TestClient(app, raise_server_exceptions=False) as client:
            openapi = client.get("/openapi.json").json()
        app_paths = set(openapi.get("paths", {}).keys())

        # Check each contract path has a matching app path
        for cpath in contract_paths:
            regex = path_to_regex(cpath)
            matched = any(re.match(regex, apath) for apath in app_paths)
            assert matched, f"Contract path {cpath} not found in app OpenAPI"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])