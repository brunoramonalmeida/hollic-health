"""In-app notifications of the signed-in user."""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from .. import clock
from ..auth import get_current_user
from ..database import get_db
from ..models import Notification, User
from ..schemas import NotificationOut, NotificationSummary

router = APIRouter(prefix="/notifications", tags=["notifications"])


def _out(row: Notification) -> NotificationOut:
    return NotificationOut(
        id=row.id,
        event=row.event,
        title=row.title,
        body=row.body,
        link=row.link,
        created_at=row.created_at,
        read_at=row.read_at,
    )


def _mine(db: Session, user: User):
    return db.query(Notification).filter(Notification.user_id == user.id)


@router.get("", response_model=list[NotificationOut])
def list_notifications(
    unread_only: bool = Query(default=False),
    limit: int = Query(default=50, ge=1, le=200),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    query = _mine(db, user)
    if unread_only:
        query = query.filter(Notification.read_at.is_(None))
    rows = query.order_by(Notification.created_at.desc(), Notification.id.desc()).limit(limit).all()
    return [_out(row) for row in rows]


@router.get("/summary", response_model=NotificationSummary)
def summary(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Badge data: unread count and the latest messages (polled by the topbar)."""
    unread = _mine(db, user).filter(Notification.read_at.is_(None)).count()
    latest = _mine(db, user).order_by(Notification.created_at.desc(), Notification.id.desc()).limit(10).all()
    return NotificationSummary(unread=unread, latest=[_out(row) for row in latest])


@router.post("/read-all", response_model=NotificationSummary)
def read_all(db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    now = clock.now().replace(microsecond=0)
    _mine(db, user).filter(Notification.read_at.is_(None)).update({Notification.read_at: now})
    db.commit()
    return summary(db, user)


@router.post("/{item_id}/read", response_model=NotificationOut)
def mark_read(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _mine(db, user).filter(Notification.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    if row.read_at is None:
        row.read_at = clock.now().replace(microsecond=0)
        db.commit()
    return _out(row)


@router.post("/{item_id}/unread", response_model=NotificationOut)
def mark_unread(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row = _mine(db, user).filter(Notification.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Notification not found")
    row.read_at = None
    db.commit()
    return _out(row)
