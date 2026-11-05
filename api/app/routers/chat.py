"""End-to-end encrypted chat of remote appointments (see app.chat).

``PUT /chat/key`` publishes the user's identity public key. Per remote
appointment: ``GET /appointments/{id}/chat`` (the conversation, who takes
part, my envelope, who still needs one, the safety code), ``POST …/keys``
(wrap the conversation key for participants), ``GET/POST …/messages``
(ciphertext in, ciphertext out), ``POST …/attachments`` + ``GET
…/attachments/{aid}`` (encrypted blobs), ``POST …/read``. The staff, who
may read the appointment, gets ``403`` here: only the two parties hold keys.
"""

import json

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi.responses import Response
from sqlalchemy.orm import Session, joinedload, selectinload

from .. import clock
from ..auth import get_current_user, require_appointment_area
from ..chat import (
    MAX_ATTACHMENT_BYTES,
    MAX_ATTACHMENTS_PER_MESSAGE,
    MAX_ORPHANS,
    ChatError,
    assert_chat_available,
    chat_state,
    fingerprint_of,
    get_or_create_conversation,
    is_participant,
    open_until,
    participants,
    public_jwk,
    read_blob,
    safety_code,
    side_of,
    store_blob,
)
from ..database import get_db
from ..models import Appointment, ChatAttachment, ChatMessage, Conversation, ConversationKey, ConversationRead, User
from ..notifications import chat_message_received
from ..schemas import (
    ChatAttachmentOut,
    ChatKeyIn,
    ChatKeyOut,
    ChatMessageIn,
    ChatMessageOut,
    ChatParticipantOut,
    ChatReadIn,
    ConversationKeyOut,
    ConversationKeysIn,
    ConversationOut,
)

keys_router = APIRouter(prefix="/chat", tags=["chat"], dependencies=[Depends(require_appointment_area)])
router = APIRouter(prefix="/appointments", tags=["chat"], dependencies=[Depends(require_appointment_area)])


# --- identity key ----------------------------------------------------------------------


def _key_out(user: User) -> ChatKeyOut:
    return ChatKeyOut(public_key=public_jwk(user.chat_public_key), fingerprint=user.chat_key_fingerprint, updated_at=user.chat_key_updated_at)


@keys_router.get("/key", response_model=ChatKeyOut)
def my_chat_key(user: User = Depends(get_current_user)):
    """The identity public key published for this login (null until the browser set one up)."""
    return _key_out(user)


@keys_router.put("/key", response_model=ChatKeyOut)
def publish_chat_key(body: ChatKeyIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Publish (or replace) the identity public key generated in the browser.
    A new key cannot open the envelopes of the old one: the other party wraps
    the conversation keys again on its next visit."""
    try:
        fingerprint = fingerprint_of(body.public_key)
    except ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    if fingerprint != user.chat_key_fingerprint:
        user.chat_public_key = json.dumps({key: body.public_key[key] for key in ("kty", "crv", "x", "y")})
        user.chat_key_fingerprint = fingerprint
        user.chat_key_updated_at = clock.now().replace(microsecond=0)
        db.commit()
    return _key_out(user)


# --- the conversation ---------------------------------------------------------------------


def _load(db: Session, item_id: int, user: User) -> tuple[Appointment, Conversation]:
    row = (
        db.query(Appointment)
        .options(joinedload(Appointment.customer), joinedload(Appointment.professional), joinedload(Appointment.specialty))
        .filter(Appointment.id == item_id)
        .first()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Appointment not found")
    if not is_participant(user, row):
        raise HTTPException(status_code=403, detail="The chat is end-to-end encrypted between the patient and the professional; only they take part")
    try:
        assert_chat_available(row)
    except ChatError as exc:
        raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
    conversation = get_or_create_conversation(db, row)
    return row, conversation


def _require_open(row: Appointment) -> None:
    is_open, reason = chat_state(row)
    if not is_open:
        raise HTTPException(status_code=409, detail=f"The chat is closed: {reason}")


def _read_marker(db: Session, conversation: Conversation, user: User) -> ConversationRead:
    marker = next((item for item in conversation.reads if item.user_id == user.id), None)
    if marker is None:
        marker = ConversationRead(conversation_id=conversation.id, user_id=user.id, last_read_message_id=0)
        db.add(marker)
        conversation.reads.append(marker)
    return marker


def _conversation_out(db: Session, row: Appointment, conversation: Conversation, user: User) -> ConversationOut:
    people = participants(db, row)
    envelopes = {item.recipient_fingerprint: item for item in conversation.keys}
    mine = envelopes.get(user.chat_key_fingerprint) if user.chat_key_fingerprint else None
    # Who still needs the key (meaningful once the key exists: the holder wraps it for them).
    pending = [person.chat_key_fingerprint for person in people if person.chat_key_fingerprint and person.chat_key_fingerprint not in envelopes] if envelopes else []
    sides = {side_of(person) for person in people if person.chat_key_fingerprint}
    code = safety_code([person.chat_key_fingerprint for person in people if person.chat_key_fingerprint]) if sides == {"customer", "professional"} else None
    last_id = max((message.id for message in conversation.messages), default=0)
    marker = next((item for item in conversation.reads if item.user_id == user.id), None)
    read_up_to = marker.last_read_message_id if marker else 0
    unread = sum(1 for message in conversation.messages if message.id > read_up_to and message.sender_id != user.id)
    is_open, reason = chat_state(row)
    return ConversationOut(
        id=conversation.id,
        appointment_id=row.id,
        open=is_open,
        closed_reason=reason,
        open_until=open_until(row),
        participants=[
            ChatParticipantOut(
                user_id=person.id,
                name=person.name,
                side=side_of(person),
                fingerprint=person.chat_key_fingerprint,
                public_key=public_jwk(person.chat_public_key),
                has_key=bool(person.chat_key_fingerprint) and person.chat_key_fingerprint in envelopes,
            )
            for person in people
        ],
        my_fingerprint=user.chat_key_fingerprint,
        my_key=(
            ConversationKeyOut(
                recipient_fingerprint=mine.recipient_fingerprint,
                ephemeral_public_key=json.loads(mine.ephemeral_public_key),
                iv=mine.iv,
                wrapped_key=mine.wrapped_key,
                created_by_id=mine.created_by_id,
            )
            if mine
            else None
        ),
        has_conversation_key=bool(envelopes),
        keys_pending=pending,
        safety_code=code,
        last_message_id=last_id,
        unread=unread,
        max_attachment_bytes=MAX_ATTACHMENT_BYTES,
        max_attachments_per_message=MAX_ATTACHMENTS_PER_MESSAGE,
    )


@router.get("/{item_id}/chat", response_model=ConversationOut)
def get_conversation(item_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The conversation of a remote appointment, as seen by one participant."""
    row, conversation = _load(db, item_id, user)
    out = _conversation_out(db, row, conversation, user)
    db.commit()
    return out


@router.post("/{item_id}/chat/keys", response_model=ConversationOut)
def share_conversation_key(item_id: int, body: ConversationKeysIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """Store the conversation key wrapped for participants' identity keys.
    The first call (no envelope yet) creates the key: it must include the
    caller's own envelope. Later calls come from a participant who holds the
    key and wraps it for those still without one; an existing envelope is
    never replaced."""
    row, conversation = _load(db, item_id, user)
    if not user.chat_key_fingerprint:
        raise HTTPException(status_code=409, detail="Set up your chat key first")
    people = {person.chat_key_fingerprint: person for person in participants(db, row) if person.chat_key_fingerprint}
    existing = {item.recipient_fingerprint for item in conversation.keys}
    if existing and user.chat_key_fingerprint not in existing:
        raise HTTPException(status_code=403, detail="You do not hold the conversation key; the other party will share it with you")
    if not existing and user.chat_key_fingerprint not in {item.recipient_fingerprint for item in body.keys}:
        raise HTTPException(status_code=400, detail="Include the envelope for your own key when creating the conversation key")
    added = 0
    for item in body.keys:
        if item.recipient_fingerprint not in people:
            raise HTTPException(status_code=400, detail="Envelopes go to the current keys of the participants only")
        if item.recipient_fingerprint in existing:
            continue  # never replaced
        try:
            fingerprint_of(item.ephemeral_public_key)
        except ChatError as exc:
            raise HTTPException(status_code=exc.status_code, detail=exc.message) from exc
        conversation.keys.append(
            ConversationKey(
                conversation_id=conversation.id,
                user_id=people[item.recipient_fingerprint].id,
                recipient_fingerprint=item.recipient_fingerprint,
                ephemeral_public_key=json.dumps(item.ephemeral_public_key),
                iv=item.iv,
                wrapped_key=item.wrapped_key,
                created_by_id=user.id,
            )
        )
        existing.add(item.recipient_fingerprint)
        added += 1
    db.flush()
    out = _conversation_out(db, row, conversation, user)
    db.commit()
    return out


# --- messages --------------------------------------------------------------------------


def _message_out(message: ChatMessage, user: User) -> ChatMessageOut:
    return ChatMessageOut(
        id=message.id,
        sender_id=message.sender_id,
        sender_name=message.sender.name,
        side=side_of(message.sender),
        mine=message.sender_id == user.id,
        ciphertext=message.ciphertext,
        iv=message.iv,
        created_at=message.created_at,
        attachments=[ChatAttachmentOut(id=item.id, size_bytes=item.size_bytes) for item in message.attachments],
    )


@router.get("/{item_id}/chat/messages", response_model=list[ChatMessageOut])
def list_messages(
    item_id: int,
    after: int = Query(default=0, ge=0, description="Only messages with an id above this one (polling)"),
    limit: int = Query(default=200, ge=1, le=500),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    row, conversation = _load(db, item_id, user)
    rows = (
        db.query(ChatMessage)
        .options(joinedload(ChatMessage.sender), selectinload(ChatMessage.attachments))
        .filter(ChatMessage.conversation_id == conversation.id, ChatMessage.id > after)
        .order_by(ChatMessage.id)
        .limit(limit)
        .all()
    )
    db.commit()  # the conversation row may have been created
    return [_message_out(message, user) for message in rows]


@router.post("/{item_id}/chat/messages", response_model=ChatMessageOut, status_code=201)
def send_message(item_id: int, body: ChatMessageIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row, conversation = _load(db, item_id, user)
    _require_open(row)
    if not conversation.keys:
        raise HTTPException(status_code=409, detail="The conversation key was not created yet")
    if user.chat_key_fingerprint not in {item.recipient_fingerprint for item in conversation.keys}:
        raise HTTPException(status_code=409, detail="You do not hold the conversation key yet; wait for the other party to share it")
    attachments = []
    if body.attachment_ids:
        if len(set(body.attachment_ids)) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise HTTPException(status_code=400, detail=f"At most {MAX_ATTACHMENTS_PER_MESSAGE} attachments per message")
        for attachment_id in dict.fromkeys(body.attachment_ids):
            attachment = db.get(ChatAttachment, attachment_id)
            if attachment is None or attachment.conversation_id != conversation.id or attachment.uploaded_by_id != user.id or attachment.message_id is not None:
                raise HTTPException(status_code=400, detail="Attachment not found, not yours, or already sent")
            attachments.append(attachment)
    message = ChatMessage(conversation_id=conversation.id, sender_id=user.id, ciphertext=body.ciphertext, iv=body.iv)
    conversation.messages.append(message)
    db.flush()
    for attachment in attachments:
        attachment.message_id = message.id
    marker = _read_marker(db, conversation, user)
    marker.last_read_message_id = message.id
    marker.read_at = clock.now().replace(microsecond=0)
    db.flush()
    chat_message_received(db, row, conversation, user)
    db.commit()
    saved = db.query(ChatMessage).options(joinedload(ChatMessage.sender), selectinload(ChatMessage.attachments)).filter(ChatMessage.id == message.id).one()
    return _message_out(saved, user)


@router.post("/{item_id}/chat/read", response_model=ConversationOut)
def mark_read(item_id: int, body: ChatReadIn, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    row, conversation = _load(db, item_id, user)
    marker = _read_marker(db, conversation, user)
    if body.last_read_message_id > marker.last_read_message_id:
        marker.last_read_message_id = body.last_read_message_id
        marker.read_at = clock.now().replace(microsecond=0)
    db.flush()
    out = _conversation_out(db, row, conversation, user)
    db.commit()
    return out


# --- attachments (encrypted blobs) ------------------------------------------------------------


@router.post("/{item_id}/chat/attachments", response_model=ChatAttachmentOut, status_code=201)
def upload_attachment(
    item_id: int,
    file: UploadFile = File(description="The attachment, already encrypted by the browser"),
    db: Session = Depends(get_db),
    user: User = Depends(get_current_user),
):
    """Store an encrypted blob; the message that references it comes next.
    The server cannot check what it is — only its size and how many are waiting."""
    row, conversation = _load(db, item_id, user)
    _require_open(row)
    content = file.file.read(MAX_ATTACHMENT_BYTES + 1)
    if not content:
        raise HTTPException(status_code=400, detail="The attachment is empty")
    if len(content) > MAX_ATTACHMENT_BYTES:
        raise HTTPException(status_code=413, detail=f"The attachment exceeds the limit of {MAX_ATTACHMENT_BYTES // (1024 * 1024)} MB")
    orphans = sum(1 for item in conversation.attachments if item.message_id is None and item.uploaded_by_id == user.id)
    if orphans >= MAX_ORPHANS:
        raise HTTPException(status_code=409, detail="Too many attachments waiting to be sent")
    attachment = ChatAttachment(conversation_id=conversation.id, uploaded_by_id=user.id, storage_key=store_blob(content), size_bytes=len(content))
    conversation.attachments.append(attachment)
    db.flush()
    out = ChatAttachmentOut(id=attachment.id, size_bytes=attachment.size_bytes)
    db.commit()
    return out


@router.get("/{item_id}/chat/attachments/{attachment_id}")
def download_attachment(item_id: int, attachment_id: int, db: Session = Depends(get_db), user: User = Depends(get_current_user)):
    """The encrypted blob, for a participant to decrypt in the browser."""
    row, conversation = _load(db, item_id, user)
    attachment = db.get(ChatAttachment, attachment_id)
    if attachment is None or attachment.conversation_id != conversation.id:
        raise HTTPException(status_code=404, detail="Attachment not found")
    content = read_blob(attachment.storage_key)
    if content is None:
        raise HTTPException(status_code=410, detail="The attachment is no longer available")
    db.commit()
    return Response(content=content, media_type="application/octet-stream", headers={"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"})
