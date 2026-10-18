"""Pydantic schemas for the import endpoints and reports."""

from datetime import datetime
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class MappingSuggestionModel(BaseModel):
    column: str
    canonical_field: Optional[str] = None
    confidence: float
    matched_on: Optional[str] = None
    ambiguous: bool = False
    alternatives: List[str] = Field(default_factory=list)


class ImportPreviewResponse(BaseModel):
    """Output of ``POST /leads/import/preview`` – nothing is stored."""

    total_rows: int
    mapping: Dict[str, Any]
    counts: Dict[str, int]
    samples: Dict[str, List[Dict[str, Any]]]
    warnings: List[str] = Field(default_factory=list)
    unmapped_columns: List[str] = Field(default_factory=list)


class ImportSummary(BaseModel):
    import_id: str
    status: str
    total_rows: int
    created: int
    duplicates: int
    rejected: int
    errors_url: str


class ImportStatusResponse(ImportSummary):
    source_type: str
    original_filename: Optional[str] = None
    mapping: Dict[str, Any] = Field(default_factory=dict)
    error_message: Optional[str] = None
    created_at: Optional[datetime] = None
    started_at: Optional[datetime] = None
    completed_at: Optional[datetime] = None


class ImportErrorModel(BaseModel):
    row_number: int
    field: Optional[str] = None
    error_code: str
    message: str
    raw_data: Dict[str, Any] = Field(default_factory=dict)


class ImportErrorListResponse(BaseModel):
    import_id: str
    total: int
    page: int
    page_size: int
    items: List[ImportErrorModel]
