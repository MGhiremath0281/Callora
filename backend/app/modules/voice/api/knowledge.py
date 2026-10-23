"""Business Knowledge Base API endpoints.

    GET    /api/v1/voice/knowledge           list knowledge items
    POST   /api/v1/voice/knowledge           create one knowledge item
    POST   /api/v1/voice/knowledge/batch     batch create knowledge items
    GET    /api/v1/voice/knowledge/{item_id} read one knowledge item
    PUT    /api/v1/voice/knowledge/{item_id} update one knowledge item
    DELETE /api/v1/voice/knowledge/{item_id} delete one knowledge item

All knowledge is scoped strictly to the authenticated business (resolved via X-API-Key).
"""

from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.modules.voice import repository as repo
from app.modules.voice.api.deps import current_business
from app.modules.voice.api.serializers import knowledge_item_payload
from app.modules.voice.constants import AuditAction
from app.modules.voice.errors import KnowledgeItemNotFoundError
from app.modules.voice.models import BusinessKnowledgeItem, prefixed_id
from app.modules.voice.schemas import (
    KnowledgeItemBatchCreate,
    KnowledgeItemCreate,
    KnowledgeItemListResponse,
    KnowledgeItemResponse,
    KnowledgeItemUpdate,
)

router = APIRouter(
    prefix="/voice/knowledge",
    tags=["Voice — Knowledge Base"],
)


def _item_or_404(session: Session, business_id: str, item_id: str) -> BusinessKnowledgeItem:
    item = repo.get_knowledge_item(session, business_id, item_id)
    if item is None:
        raise KnowledgeItemNotFoundError(item_id)
    return item


@router.get("", response_model=KnowledgeItemListResponse, status_code=status.HTTP_200_OK)
async def list_knowledge(
    business_id: str = Depends(current_business),
    category: Optional[str] = Query(default=None),
    is_active: Optional[bool] = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    offset: int = Query(default=0, ge=0),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """List verified knowledge items belonging to the authenticated business."""
    items = repo.list_knowledge_items(
        db,
        business_id=business_id,
        category=category,
        is_active=is_active,
        limit=limit,
        offset=offset,
    )
    serialized = [knowledge_item_payload(it) for it in items]
    return {
        "items": serialized,
        "total": len(serialized),
        "business_id": business_id,
    }


@router.post("", response_model=KnowledgeItemResponse, status_code=status.HTTP_201_CREATED)
async def create_knowledge(
    payload: KnowledgeItemCreate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Store a verified business FAQ, feature, pricing rule, or policy."""
    item = BusinessKnowledgeItem(
        id=prefixed_id("kb"),
        business_id=business_id,
        category=payload.category,
        title=payload.title,
        content=payload.content,
        tags=list(payload.tags or []),
        source_reference=payload.source_reference,
        is_active=payload.is_active,
        priority=payload.priority,
        extra_metadata=dict(payload.extra_metadata or {}),
    )
    created = repo.create_knowledge_item(db, item)
    repo.add_audit_log(
        db,
        action=AuditAction.KNOWLEDGE_CREATED,
        business_id=business_id,
        actor="api",
        entity_type="knowledge_item",
        entity_id=created.id,
        decision="allow",
        reason="Knowledge item created",
        metadata={"category": created.category, "title": created.title},
    )
    db.commit()
    return knowledge_item_payload(created)


@router.post("/batch", response_model=KnowledgeItemListResponse, status_code=status.HTTP_201_CREATED)
async def batch_create_knowledge(
    payload: KnowledgeItemBatchCreate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Batch upload verified knowledge items for initial setup or migrations."""
    created_items = []
    for entry in payload.items:
        item = BusinessKnowledgeItem(
            id=prefixed_id("kb"),
            business_id=business_id,
            category=entry.category,
            title=entry.title,
            content=entry.content,
            tags=list(entry.tags or []),
            source_reference=entry.source_reference,
            is_active=entry.is_active,
            priority=entry.priority,
            extra_metadata=dict(entry.extra_metadata or {}),
        )
        created = repo.create_knowledge_item(db, item)
        created_items.append(created)

    repo.add_audit_log(
        db,
        action=AuditAction.KNOWLEDGE_CREATED,
        business_id=business_id,
        actor="api",
        entity_type="knowledge_batch",
        entity_id=business_id,
        decision="allow",
        reason=f"Batch imported {len(created_items)} knowledge items",
        metadata={"count": len(created_items)},
    )
    db.commit()
    return {
        "items": [knowledge_item_payload(it) for it in created_items],
        "total": len(created_items),
        "business_id": business_id,
    }


@router.get("/{item_id}", response_model=KnowledgeItemResponse, status_code=status.HTTP_200_OK)
async def read_knowledge(
    item_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Read one verified knowledge item."""
    item = _item_or_404(db, business_id, item_id)
    return knowledge_item_payload(item)


@router.put("/{item_id}", response_model=KnowledgeItemResponse, status_code=status.HTTP_200_OK)
async def update_knowledge(
    item_id: str,
    payload: KnowledgeItemUpdate,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
) -> Dict[str, Any]:
    """Update a verified knowledge item."""
    item = _item_or_404(db, business_id, item_id)
    values = payload.model_dump(exclude_unset=True, exclude_none=True)
    updated = repo.update_knowledge_item(db, item, values)
    repo.add_audit_log(
        db,
        action=AuditAction.KNOWLEDGE_UPDATED,
        business_id=business_id,
        actor="api",
        entity_type="knowledge_item",
        entity_id=item_id,
        decision="allow",
        reason="Knowledge item updated",
        metadata={"updated_fields": list(values.keys())},
    )
    db.commit()
    return knowledge_item_payload(updated)


@router.delete("/{item_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_knowledge(
    item_id: str,
    business_id: str = Depends(current_business),
    db: Session = Depends(get_db),
) -> None:
    """Delete a verified knowledge item."""
    item = _item_or_404(db, business_id, item_id)
    repo.delete_knowledge_item(db, item)
    repo.add_audit_log(
        db,
        action=AuditAction.KNOWLEDGE_DELETED,
        business_id=business_id,
        actor="api",
        entity_type="knowledge_item",
        entity_id=item_id,
        decision="allow",
        reason="Knowledge item deleted",
    )
    db.commit()
