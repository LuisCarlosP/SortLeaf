from datetime import datetime
from uuid import UUID
from fastapi import APIRouter, Depends, HTTPException, Query, Response
from core.identity import Identity, require_account

router = APIRouter(prefix="/activity", tags=["Activity"])


@router.get("")
def recent_activity(before: datetime | None = None, before_id: UUID | None = None,
                    limit: int = Query(50, ge=1, le=100), owner: Identity = Depends(require_account)):
    query = (owner.db.table("activity_events").select("id,materials,confidence,recyclable,created_at")
             .eq("owner_id", owner.id))
    if (before is None) != (before_id is None):
        raise HTTPException(422, "invalid_cursor")
    if before is not None:
        cursor = before.isoformat()
        query = query.or_(f"created_at.lt.{cursor},and(created_at.eq.{cursor},id.lt.{before_id})")
    return query.order("created_at", desc=True).order("id", desc=True).limit(limit).execute().data


@router.delete("", status_code=204)
def clear_activity(owner: Identity = Depends(require_account)):
    owner.db.table("activity_events").delete(returning="minimal").eq("owner_id", owner.id).execute()
    return Response(status_code=204)


@router.delete("/{event_id}", status_code=204)
def delete_activity(event_id: UUID, owner: Identity = Depends(require_account)):
    owner.db.table("activity_events").delete(returning="minimal").eq("owner_id", owner.id).eq("id", str(event_id)).execute()
    return Response(status_code=204)
