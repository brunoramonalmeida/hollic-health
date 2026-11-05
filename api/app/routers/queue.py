"""Delivery queue administration (admins only)."""

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..auth import require_admin
from ..database import get_db
from ..models import QueueJob
from ..broker import BROKER_URL, get_broker
from ..queue import DEAD, PENDING, STATUSES, retry_job, stats
from ..schemas import QueueJobOut, QueueStats, WorkerRunOut
from ..worker import tick

router = APIRouter(prefix="/queue", tags=["queue"], dependencies=[Depends(require_admin)])


def _out(row: QueueJob) -> QueueJobOut:
    payload = json.loads(row.payload or "{}")
    return QueueJobOut(
        id=row.id,
        kind=row.kind,
        event=row.event,
        status=row.status,
        attempts=row.attempts,
        max_attempts=row.max_attempts,
        run_at=row.run_at,
        last_error=row.last_error or "",
        created_at=row.created_at,
        finished_at=row.finished_at,
        to=payload.get("to"),
        subject=payload.get("subject"),
    )


@router.get("", response_model=list[QueueJobOut])
def list_jobs(
    status: str | None = Query(default=None),
    limit: int = Query(default=100, ge=1, le=500),
    db: Session = Depends(get_db),
):
    if status is not None and status not in STATUSES:
        raise HTTPException(status_code=400, detail=f"status must be one of {', '.join(STATUSES)}")
    query = db.query(QueueJob)
    if status is not None:
        query = query.filter(QueueJob.status == status)
    rows = query.order_by(QueueJob.id.desc()).limit(limit).all()
    return [_out(row) for row in rows]


@router.get("/stats", response_model=QueueStats)
def queue_stats(db: Session = Depends(get_db)):
    try:
        depth = get_broker().depth()
    except Exception:  # noqa: BLE001 - the broker may be down; the stats page must still load
        depth = None
    return QueueStats(**stats(db), broker="memory" if BROKER_URL == "memory" else "rabbitmq", broker_depth=depth)


@router.post("/run", response_model=WorkerRunOut)
def run_now(db: Session = Depends(get_db)):
    """Run one worker pass immediately (reminders + due jobs)."""
    return WorkerRunOut(**tick(db))


@router.post("/{item_id}/retry", response_model=QueueJobOut)
def retry(item_id: int, db: Session = Depends(get_db)):
    row = db.get(QueueJob, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Job not found")
    if row.status not in (DEAD, PENDING):
        raise HTTPException(status_code=409, detail="Only dead or pending jobs can be retried")
    retry_job(db, row)
    db.commit()
    return _out(row)


@router.post("/retry-dead", response_model=QueueStats)
def retry_all_dead(db: Session = Depends(get_db)):
    for row in db.query(QueueJob).filter(QueueJob.status == DEAD).all():
        retry_job(db, row)
    db.commit()
    return QueueStats(**stats(db))
