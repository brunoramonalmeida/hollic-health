"""Event notifications.

``notify(db, event, ...)`` turns a business event into:

* one in-app ``Notification`` row per recipient user, and
* one queued email job per recipient address (see ``queue``),

all inside the caller's transaction. Recipients:

* the **customer** of the record: every client user linked to it (in-app)
  and the customer's email address;
* the **staff** (admins, supervisors, attendants) for events started by a
  client, so the clinic knows what clients did on their own;
* the **professional** of the appointment: every professional login linked
  to it (in-app), for changes of its agenda, arrivals and visits awaiting
  its signature;
* the **seller** of a sale: every seller login linked to it (in-app) and
  the seller's own email address, for its sales, the commissions it earns
  and changes to its region or its record;
* the **partner company** of a corporate agreement: its portal logins
  (in-app) and its contact email, for its invoices and its minimum of
  employees;
* a specific **user** (e.g. the one whose login was just created).
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Session

from . import clock
from .addresses import format_address, maps_url
from .auth import ROLE_ADMIN, ROLE_CLIENT, ROLE_PARTNER, ROLE_PROFESSIONAL, ROLE_SELLER, STAFF_ROLES
from .models import Agreement, Appointment, Customer, Installment, Notification, Region, SaleChange, Seller, Subscription, User
from .billing import dependents_count, monthly_amount, part_cents
from .partners import PAYER_COMPANY, PAYER_SHARED, payer_label, share_on, split_cents
from .sales import commission_cents, format_bp, scope_label
from .queue import KIND_EMAIL, enqueue

# Event keys (also used by the front-end to pick icons/links).
APPOINTMENT_CREATED = "appointment.created"
APPOINTMENT_RESCHEDULED = "appointment.rescheduled"
APPOINTMENT_CANCELLED = "appointment.cancelled"
APPOINTMENT_COMPLETED = "appointment.completed"
APPOINTMENT_PRESCRIPTION = "appointment.prescription"  # a draft attached: the professional is asked to sign
APPOINTMENT_PRESCRIPTION_SIGNED = "appointment.prescription_signed"  # signed: the patient may use it
CHAT_MESSAGE = "chat.message"  # a new message in the chat of a remote appointment (no content: it is encrypted)
APPOINTMENT_REMINDER_DAY = "appointment.reminder_day"
APPOINTMENT_REMINDER_HOUR = "appointment.reminder_hour"
APPOINTMENT_CHECKED_IN = "appointment.checked_in"
APPOINTMENT_CHECKED_OUT = "appointment.checked_out"
APPOINTMENT_STARTED = "appointment.started"
CARD_LOCKED = "card.locked"
CARD_RESET = "card.reset"
PAYOUT_ADVANCE = "payout.advance"
PAYOUT_SETTLEMENT = "payout.settlement"
PAYOUT_PAID = "payout.paid"
PAYOUT_CONFIRMED = "payout.confirmed"
SUBSCRIPTION_CREATED = "subscription.created"
SUBSCRIPTION_CANCELLED = "subscription.cancelled"
INSTALLMENT_PAID = "installment.paid"
USER_CREATED = "user.created"
SALE_RECORDED = "sale.recorded"
SUBSCRIPTION_UPGRADED = "subscription.upgraded"
SALE_CANCELLED = "sale.cancelled"
COMMISSION_EARNED = "commission.earned"
REGION_UPDATED = "region.updated"
SELLER_MOVED = "seller.moved"
SELLER_STATUS = "seller.status"
SALE_CHANGE_REQUESTED = "sale.change_requested"
SALE_CHANGE_CONFIRMED = "sale.change_confirmed"
SALE_CHANGE_REJECTED = "sale.change_rejected"
SALE_CHANGE_WITHDRAWN = "sale.change_withdrawn"
TEAM_SALE_RECORDED = "team.sale_recorded"
OVERRIDE_EARNED = "override.earned"
SELLER_LEADER_CHANGED = "seller.leader_changed"
AGREEMENT_CONDITIONS = "agreement.conditions"  # minimum of employees reached / no longer met
AGREEMENT_INVOICE_PAID = "agreement.invoice_paid"
AGREEMENT_ENROLLMENT = "agreement.enrollment"  # the company enrolled / removed an employee in its portal
SHARE_CHANGED = "agreement.share_changed"  # co-payment: the split of an employee's fees changed
INSTALLMENT_PART_PAID = "installment.part_paid"  # one part of a shared fee was paid
EMPLOYEE_UPDATED = "agreement.employee_updated"  # the company changed an employee's data, plan or dependents


@dataclass(frozen=True)
class Message:
    event: str
    title: str
    body: str
    link: str = ""


def money(cents: int) -> str:
    units, rest = divmod(cents, 100)
    return f"R$ {units:,}".replace(",", ".") + f",{rest:02d}"


def when(value: datetime) -> str:
    return value.strftime("%d/%m/%Y at %H:%M")


# --- delivery ---------------------------------------------------------------------


def deliver(db: Session, message: Message, *, users: list[User] = (), emails: list[str] = ()) -> list[Notification]:
    """Create the in-app rows and queue the emails. Duplicates are removed."""
    created = []
    seen_users: set[int] = set()
    for user in users:
        if user is None or user.id in seen_users:
            continue
        seen_users.add(user.id)
        row = Notification(
            user_id=user.id, event=message.event, title=message.title, body=message.body, link=message.link
        )
        db.add(row)
        created.append(row)
    seen_emails: set[str] = set()
    for email in emails:
        address = (email or "").strip().lower()
        if not address or address in seen_emails:
            continue
        seen_emails.add(address)
        enqueue(
            db,
            KIND_EMAIL,
            {"to": address, "subject": message.title, "body": message.body},
            event=message.event,
        )
    return created


def customer_users(db: Session, customer_id: int) -> list[User]:
    return db.query(User).filter(User.role == ROLE_CLIENT, User.customer_id == customer_id).all()


def staff_users(db: Session) -> list[User]:
    return db.query(User).filter(User.role.in_(STAFF_ROLES)).all()


def customer_emails(db: Session, customer: Customer) -> list[str]:
    """The customer's own address plus the addresses of its client logins."""
    addresses = [customer.email] if customer.email else []
    addresses += [user.email for user in customer_users(db, customer.id)]
    return addresses


def notify_customer(db: Session, customer: Customer, message: Message) -> None:
    deliver(db, message, users=customer_users(db, customer.id), emails=customer_emails(db, customer))


def notify_staff(db: Session, message: Message) -> None:
    deliver(db, message, users=staff_users(db))


def professional_users(db: Session, professional_id: int) -> list[User]:
    return db.query(User).filter(User.role == ROLE_PROFESSIONAL, User.professional_id == professional_id).all()


def notify_professional(db: Session, row: Appointment, message: Message) -> None:
    deliver(db, message, users=professional_users(db, row.professional_id))


def admin_users(db: Session) -> list[User]:
    return db.query(User).filter(User.role == ROLE_ADMIN).all()


def seller_users(db: Session, seller_id: int) -> list[User]:
    return db.query(User).filter(User.role == ROLE_SELLER, User.seller_id == seller_id).all()


def seller_emails(db: Session, seller: Seller) -> list[str]:
    """The seller's own address plus the addresses of its logins."""
    addresses = [seller.email] if seller.email else []
    addresses += [user.email for user in seller_users(db, seller.id)]
    return addresses


def notify_seller(db: Session, seller: Seller, message: Message) -> None:
    deliver(db, message, users=seller_users(db, seller.id), emails=seller_emails(db, seller))


# --- payout events ------------------------------------------------------------------


def _payee_users(db: Session, payment) -> list[User]:
    if payment.payee_type == "professional":
        return professional_users(db, payment.professional_id)
    return []


def payout_advance_registered(db: Session, payment, payee_name: str, actor: User) -> None:
    deliver(
        db,
        Message(
            PAYOUT_ADVANCE,
            f"Advance of {money(payment.amount_cents)} paid on {payment.paid_on:%d/%m/%Y} — sign the receipt",
            f"Receipt {payment.receipt_number}: advance registered by {actor.name}, to be deducted from your next settlement.\n"
            "Please sign the receipt to confirm you received the amount.",
            f"/receipts/{payment.id}",
        ),
        users=_payee_users(db, payment),
    )


def payout_settlement_created(db: Session, settlement, payee_name: str, actor_id: int | None) -> None:
    """``actor_id`` is None for the automatic monthly closing."""
    if settlement.payee_type != "professional":
        return
    installments = settlement.installments_count
    plan = f"in {installments} installment(s)" if installments else "fully covered by advances"
    actor = db.get(User, actor_id) if actor_id is not None else None
    origin = f"Created by {actor.name}." if actor is not None else "Automatic monthly closing."
    deliver(
        db,
        Message(
            PAYOUT_SETTLEMENT,
            f"Settlement {settlement.period_start:%d/%m/%Y} – {settlement.period_end:%d/%m/%Y}: {money(settlement.net_cents)} to receive",
            f"{settlement.appointments_count} signed appointments, earned {money(settlement.earned_cents)}, "
            f"advances deducted {money(settlement.advances_cents)}, net {money(settlement.net_cents)} {plan}.\n"
            f"{origin}",
            f"/my-payouts",
        ),
        users=professional_users(db, settlement.professional_id),
    )


def payout_installment_paid(db: Session, payment, payee_name: str, actor: User) -> None:
    deliver(
        db,
        Message(
            PAYOUT_PAID,
            f"Installment {payment.sequence} paid: {money(payment.amount_cents)} on {payment.paid_on:%d/%m/%Y} — sign the receipt",
            f"Receipt {payment.receipt_number}, registered by {actor.name}. Please sign it to confirm you received the amount.",
            f"/receipts/{payment.id}",
        ),
        users=_payee_users(db, payment),
    )


def payout_receipt_confirmed(db: Session, payment, actor: User) -> None:
    """The payee signed: administrators are told the money was acknowledged."""
    deliver(
        db,
        Message(
            PAYOUT_CONFIRMED,
            f"Receipt {payment.receipt_number} signed by {actor.name} ({money(payment.amount_cents)})",
            "The payee confirmed the amount was received.",
            f"/receipts/{payment.id}",
        ),
        users=admin_users(db),
    )


# --- appointment events -----------------------------------------------------------


def _patient(row: Appointment) -> str:
    return row.dependent.name if row.dependent else row.customer.name


def _where(row: Appointment) -> str:
    clinic = row.clinic
    if getattr(row, "remote", False):
        lines = ["Where: remote appointment (video call) — no need to go to the clinic"]
        if row.meeting_url:
            lines.append(f"Join the call: {row.meeting_url}")
        lines.append(f"Professional's clinic: {clinic.name}" + (f" · phone {clinic.phone}" if clinic.phone else ""))
        return "\n".join(lines)
    lines = [f"Where: {clinic.name}"]
    address = format_address(clinic)
    if address:
        lines.append(f"Address: {address}")
    if clinic.phone:
        lines.append(f"Phone: {clinic.phone}")
    if clinic.directions:
        lines.append(f"How to get there: {clinic.directions}")
    link = maps_url(clinic)
    if link:
        lines.append(f"Map: {link}")
    return "\n".join(lines)


def _plan_allowance(limit: int, period: str) -> str:
    if not limit:
        return "unlimited appointments"
    unit = "per month" if period == "month" else "in the subscription period"
    return f"{limit} appointment{'s' if limit != 1 else ''} {unit}"


def _appointment_details(row: Appointment) -> str:
    if row.over_limit:
        discount = f" (full price: the plan's allowance of {_plan_allowance(row.plan_limit, row.plan_limit_period)} is already used)"
    else:
        discount = f" ({row.discount_percent}% plan discount)" if row.discount_percent else ""
    service = (
        "On-call service: yes (on-call rates)\n"
        if row.on_call
        else "Urgent visit: yes (urgent rate)\n"
        if row.urgent
        else "Remote appointment: yes (video call, remote rates)\n"
        if getattr(row, "remote", False)
        else ""
    )
    closing = (
        "Please confirm your presence with the check-in when you join the call, and the check-out when the visit ends. "
        "Keep your virtual card at hand: the professional will ask for its code to start the visit."
        if getattr(row, "remote", False)
        else "Please confirm your arrival with the check-in when you get there, and the check-out when the visit ends."
    )
    return (
        f"Patient: {_patient(row)}\n"
        f"When: {when(row.marked_at)}\n"
        f"Professional: {row.professional.name} ({row.specialty.name})\n"
        f"{service}"
        f"{_where(row)}\n"
        f"Amount: {money(row.amount_cents)}{discount}\n\n"
        f"{closing}"
    )


def _kind(row: Appointment) -> str:
    """'Remote appointment' or 'Appointment', for titles."""
    return "Remote appointment" if getattr(row, "remote", False) else "Appointment"


def appointment_created(db: Session, row: Appointment, actor: User) -> None:
    message = Message(
        APPOINTMENT_CREATED,
        f"{_kind(row)} scheduled for {when(row.marked_at)}",
        _appointment_details(row),
        f"/appointments/{row.id}",
    )
    notify_customer(db, row.customer, message)
    notify_professional(
        db,
        row,
        Message(
            APPOINTMENT_CREATED,
            f"New {_kind(row).lower()}: {_patient(row)} on {when(row.marked_at)}",
            _appointment_details(row),
            f"/appointments/{row.id}",
        ),
    )
    if actor.role == ROLE_CLIENT:
        notify_staff(
            db,
            Message(
                APPOINTMENT_CREATED,
                f"{row.customer.name} booked an appointment for {when(row.marked_at)}",
                _appointment_details(row),
                f"/appointments/{row.id}",
            ),
        )


def appointment_changed(db: Session, row: Appointment, previous: dict, actor: User) -> None:
    """Called after an edit; decides which event (if any) it represents."""
    if previous["status"] != "cancelada" and row.status == "cancelada":
        message = Message(
            APPOINTMENT_CANCELLED,
            f"Appointment of {when(row.marked_at)} cancelled",
            _appointment_details(row),
            f"/appointments/{row.id}",
        )
        notify_customer(db, row.customer, message)
        notify_professional(
            db,
            row,
            Message(
                APPOINTMENT_CANCELLED,
                f"Cancelled: {_patient(row)} on {when(row.marked_at)}",
                _appointment_details(row),
                f"/appointments/{row.id}",
            ),
        )
        if actor.role == ROLE_CLIENT:
            notify_staff(
                db,
                Message(
                    APPOINTMENT_CANCELLED,
                    f"{row.customer.name} cancelled the appointment of {when(row.marked_at)}",
                    _appointment_details(row),
                    f"/appointments/{row.id}",
                ),
            )
        return
    if previous["status"] != "realizada" and row.status == "realizada":
        _completed(db, row)
        return
    kind_changed = previous.get("remote") is not None and bool(previous["remote"]) != bool(getattr(row, "remote", False))
    if previous["marked_at"] != row.marked_at or previous["professional_id"] != row.professional_id or kind_changed:
        if previous["marked_at"] != row.marked_at:
            title, header = f"{_kind(row)} moved to {when(row.marked_at)}", f"Previously: {when(previous['marked_at'])}"
        elif kind_changed:
            title = f"{_kind(row)} on {when(row.marked_at)}: now {'a video call' if row.remote else 'in person'}"
            header = f"Previously: {'remote (video call)' if previous['remote'] else 'in person'}"
        else:
            title, header = f"{_kind(row)} moved to {when(row.marked_at)}", f"Previously: {when(previous['marked_at'])}"
        message = Message(
            APPOINTMENT_RESCHEDULED,
            title,
            f"{header}\n\n" + _appointment_details(row),
            f"/appointments/{row.id}",
        )
        notify_customer(db, row.customer, message)
        notify_professional(
            db,
            row,
            Message(
                APPOINTMENT_RESCHEDULED,
                f"Moved: {_patient(row)} to {when(row.marked_at)}",
                message.body,
                f"/appointments/{row.id}",
            ),
        )
        if actor.role == ROLE_CLIENT:
            notify_staff(
                db,
                Message(
                    APPOINTMENT_RESCHEDULED,
                    f"{row.customer.name} moved an appointment to {when(row.marked_at)}",
                    message.body,
                    f"/appointments/{row.id}",
                ),
            )


def appointment_checked_in(db: Session, row: Appointment, actor: User) -> None:
    """Arrival confirmed: the customer gets a receipt, the staff is told who is waiting."""
    at = row.checked_in_at.strftime("%H:%M")
    notify_customer(
        db,
        row.customer,
        Message(
            APPOINTMENT_CHECKED_IN,
            f"Check-in confirmed at {at}",
            f"Arrival registered for the appointment of {when(row.marked_at)} with {row.professional.name}.\n"
            "When the visit ends, please confirm the check-out.",
            f"/appointments/{row.id}",
        ),
    )
    notify_staff(
        db,
        Message(
            APPOINTMENT_CHECKED_IN,
            f"{_patient(row)} checked in at {at} ({row.professional.name}, {row.marked_at:%H:%M})",
            f"Clinic: {row.clinic.name}\nRegistered by: {actor.name}",
            f"/appointments/{row.id}",
        ),
    )
    notify_professional(
        db,
        row,
        Message(
            APPOINTMENT_CHECKED_IN,
            f"{_patient(row)} is waiting (checked in at {at} for {row.marked_at:%H:%M})",
            f"Clinic: {row.clinic.name}\nRegistered by: {actor.name}",
            f"/appointments/{row.id}",
        ),
    )


def appointment_started(db: Session, row: Appointment, actor: User) -> None:
    """The patient's card code was verified: the patient is told (so an
    impostor using a leaked code is noticed at once)."""
    at = row.started_at.strftime("%H:%M")
    notify_customer(
        db,
        row.customer,
        Message(
            APPOINTMENT_STARTED,
            f"Appointment started at {at} — card verified",
            f"The visit of {_patient(row)} with {row.professional.name} ({when(row.marked_at)}) started at {at}, "
            f"after the virtual card code was verified by {actor.name}.\n"
            "If this was not you, contact the clinic and reset your card.",
            f"/appointments/{row.id}",
        ),
    )


def card_locked(db: Session, row: Appointment, actor: User) -> None:
    """Too many wrong codes: the patient and the staff are alerted."""
    until = row.start_locked_until.strftime("%H:%M")
    notify_customer(
        db,
        row.customer,
        Message(
            CARD_LOCKED,
            f"Wrong card codes for the appointment of {when(row.marked_at)}",
            f"Several wrong codes were typed to start the visit of {_patient(row)} with {row.professional.name}. "
            f"Starting is locked until {until}.\nIf you are not at the clinic, contact us and reset your card.",
            f"/appointments/{row.id}",
        ),
    )
    notify_staff(
        db,
        Message(
            CARD_LOCKED,
            f"Card locked: {_patient(row)}, {when(row.marked_at)} ({row.professional.name})",
            f"Too many wrong codes typed by {actor.name}. Locked until {until}.",
            f"/appointments/{row.id}",
        ),
    )


def card_reset(db: Session, customer: Customer, patient_name: str, actor: User) -> None:
    notify_customer(
        db,
        customer,
        Message(
            CARD_RESET,
            f"Virtual card of {patient_name} renewed",
            f"A new virtual card was issued for {patient_name} by {actor.name}. The previous codes no longer work.",
            "/my-card",
        ),
    )


def appointment_checked_out(db: Session, row: Appointment, actor: User) -> None:
    """Visit ended: the customer gets a receipt; the professional is asked to sign."""
    at = row.checked_out_at.strftime("%H:%M")
    notify_customer(
        db,
        row.customer,
        Message(
            APPOINTMENT_CHECKED_OUT,
            f"Check-out confirmed at {at}",
            f"End of the visit registered for the appointment of {when(row.marked_at)} with {row.professional.name}.\n"
            "The appointment is completed once the professional signs it.",
            f"/appointments/{row.id}",
        ),
    )
    notify_professional(
        db,
        row,
        Message(
            APPOINTMENT_CHECKED_OUT,
            f"Awaiting your signature: {_patient(row)}, {when(row.marked_at)}",
            f"The visit ended at {at} (registered by {actor.name}). Sign the appointment to complete it.",
            f"/appointments/{row.id}",
        ),
    )


def _completed(db: Session, row: Appointment) -> None:
    notify_customer(
        db,
        row.customer,
        Message(
            APPOINTMENT_COMPLETED,
            f"Appointment of {when(row.marked_at)} completed",
            "Thank you for your visit.\n\n" + _appointment_details(row),
            f"/appointments/{row.id}",
        ),
    )


def appointment_signed(db: Session, row: Appointment, previous: dict, actor: User) -> None:
    """The professional signed: the appointment is completed."""
    if previous["status"] != "realizada":
        _completed(db, row)


def prescription_attached(db: Session, row: Appointment, document, actor: User) -> None:
    """A prescription file was attached by someone else than the
    professional: the professional is asked to sign it (the patient is told
    once it is signed, see ``prescription_signed``)."""
    if actor.role == ROLE_PROFESSIONAL and actor.professional_id == row.professional_id:
        return
    body = (
        f"File: {document.filename}\n"
        f"Attached by: {actor.name}\n"
        f"Appointment: {when(row.marked_at)} with {row.professional.name} ({row.specialty.name})\n"
        f"Patient: {_patient(row)}\n\n"
        "The prescription is valid for pharmacies only once you sign it with your ICP-Brasil certificate; open the appointment to sign it."
    )
    notify_professional(
        db,
        row,
        Message(APPOINTMENT_PRESCRIPTION, f"Prescription awaiting your signature: {_patient(row)}, {when(row.marked_at)}", body, f"/appointments/{row.id}"),
    )


def prescription_signed(db: Session, row: Appointment, document, actor: User) -> None:
    """The professional signed the prescription: the patient may use it."""
    body = (
        f"File: {document.filename}\n"
        f"Signed by: {document.signer_name} (ICP-Brasil certificate, CPF {document.signer_document}) at {when(document.signed_at)}\n"
        f"Appointment: {when(row.marked_at)} with {row.professional.name} ({row.specialty.name})\n"
        f"Patient: {_patient(row)}\n\n"
        "Open the appointment to download the prescription and its signature file (.p7s); the pharmacy can verify both. "
        "Download links are valid for a limited time; open the appointment again for a new one."
    )
    notify_customer(
        db,
        row.customer,
        Message(APPOINTMENT_PRESCRIPTION_SIGNED, f"Signed prescription available for the appointment of {when(row.marked_at)}", body, f"/appointments/{row.id}"),
    )


def chat_message_received(db: Session, row: Appointment, conversation, sender: User) -> None:
    """A message arrived in the end-to-end chat: the other side is told there
    is something to read (never what — the server cannot read it), at most
    once every ``NOTIFY_INTERVAL`` per side."""
    from .chat import NOTIFY_INTERVAL  # local import: app.chat imports this module's neighbours

    now = clock.now().replace(microsecond=0)
    body = (
        f"New message in the chat of the remote appointment of {when(row.marked_at)} with {row.professional.name} ({row.specialty.name}).\n"
        "Open the appointment to read it: the chat is end-to-end encrypted, so only the patient and the professional can."
    )
    if sender.role == ROLE_PROFESSIONAL:
        if conversation.customer_notified_at and now - conversation.customer_notified_at < NOTIFY_INTERVAL:
            return
        conversation.customer_notified_at = now
        notify_customer(db, row.customer, Message(CHAT_MESSAGE, f"{row.professional.name} sent you a message", body, f"/appointments/{row.id}"))
    else:
        if conversation.professional_notified_at and now - conversation.professional_notified_at < NOTIFY_INTERVAL:
            return
        conversation.professional_notified_at = now
        notify_professional(db, row, Message(CHAT_MESSAGE, f"{_patient(row)} sent you a message", body, f"/appointments/{row.id}"))


def appointment_reminder(db: Session, row: Appointment, hours_ahead: int) -> None:
    if hours_ahead >= 24:
        event, title = APPOINTMENT_REMINDER_DAY, f"Reminder: appointment tomorrow at {row.marked_at:%H:%M}"
    else:
        event, title = APPOINTMENT_REMINDER_HOUR, f"Reminder: appointment in 1 hour ({row.marked_at:%H:%M})"
    notify_customer(db, row.customer, Message(event, title, _appointment_details(row), f"/appointments/{row.id}"))


# --- subscription events ------------------------------------------------------------


def _subscription_details(row: Subscription) -> str:
    corporate = ""
    if row.agreement is not None:
        share = share_on(row.shares, row.joined_on) if row.agreement.payer == PAYER_SHARED else None
        corporate = (
            f"Corporate plan: {row.agreement.partner.name} (agreement {row.agreement.name}, "
            f"{payer_label(row.agreement.payer, share)})\n"
        )
    return (
        f"Plan: {row.modality.name}\n"
        + corporate
        + f"Period: {row.joined_on:%d/%m/%Y} to {row.expires_on:%d/%m/%Y}\n"
        f"Appointment discount: {row.modality.discount_percent}%\n"
        f"Covered appointments: {_plan_allowance(row.modality.appointment_limit, row.modality.appointment_limit_period)}"
        + (" (over the limit, appointments pay the full price)" if row.modality.appointment_limit else "")
    )


def subscription_created(db: Session, row: Subscription, actor: User) -> None:
    first = min(row.installments, key=lambda item: item.sequence, default=None)
    body = _subscription_details(row)
    if first is not None:
        body += f"\nFirst installment: {money(first.amount_cents)} due on {first.due_on:%d/%m/%Y}"
    if row.agreement is not None and row.agreement.payer == PAYER_COMPANY:
        body += f"\n\nYour fees are paid by {row.agreement.partner.name}: the plan becomes active once the company pays the first one."
    elif row.agreement is not None and row.agreement.payer == PAYER_SHARED:
        body += (
            f"\n\nCo-payment: {row.agreement.partner.name} pays its part of each fee and you pay yours; "
            "the plan becomes active once both parts of the first fee are paid. Follow them in My payments."
        )
    else:
        body += "\n\nThe plan becomes active once the first installment is paid."
    notify_customer(db, row.customer, Message(SUBSCRIPTION_CREATED, f"Welcome to {row.modality.name}", body, f"/subscriptions/{row.id}"))
    if actor.role == ROLE_CLIENT:
        notify_staff(
            db,
            Message(
                SUBSCRIPTION_CREATED,
                f"{row.customer.name} subscribed to {row.modality.name}",
                _subscription_details(row),
                f"/subscriptions/{row.id}",
            ),
        )
    if row.seller is not None:
        sale_recorded(db, row, actor)


# --- sales (sellers) -------------------------------------------------------------------


def _sale_details(row: Subscription) -> str:
    monthly = min(row.installments, key=lambda item: item.sequence, default=None)
    fee = f"Monthly fee: {money(monthly.amount_cents)}" if monthly is not None else ""
    if monthly is not None and monthly.discount_cents:
        fee += f" ({row.discount_percent}% special discount of region {row.region.name})"
    return (
        f"Customer: {row.customer.name}\n"
        f"Plan: {row.modality.name}\n"
        f"Period: {row.joined_on:%d/%m/%Y} to {row.expires_on:%d/%m/%Y}\n"
        f"{fee}\n"
        f"Region of the sale: {row.region.name if row.region else '-'} · commission {format_bp(row.commission_bp or 0)} "
        "on every installment paid"
    )


def sale_recorded(db: Session, row: Subscription, actor: User) -> None:
    """A subscription was sold by a seller: the seller is told (and the staff
    when the seller recorded it on its own)."""
    seller = row.seller
    notify_seller(
        db,
        seller,
        Message(
            SALE_RECORDED,
            f"Sale recorded: {row.customer.name} — {row.modality.name}",
            _sale_details(row) + "\n\nThe commission is earned as the customer pays each installment.",
            f"/subscriptions/{row.id}",
        ),
    )
    if actor.role == ROLE_SELLER:
        notify_staff(
            db,
            Message(
                SALE_RECORDED,
                f"{seller.name} sold {row.modality.name} to {row.customer.name}",
                _sale_details(row),
                f"/subscriptions/{row.id}",
            ),
        )
    for item in row.overrides:
        notify_seller(
            db,
            item.seller,
            Message(
                TEAM_SALE_RECORDED,
                f"Team sale: {seller.name} sold {row.modality.name} to {row.customer.name}",
                _sale_details(row)
                + f"\nYour override (level {item.level}): {format_bp(item.rate_bp)} on every installment paid, "
                f"on top of {seller.name}'s commission.",
                f"/subscriptions/{row.id}",
            ),
        )


def sale_cancelled(db: Session, row: Subscription, actor: User) -> None:
    notify_seller(
        db,
        row.seller,
        Message(
            SALE_CANCELLED,
            f"Sale cancelled: {row.customer.name} — {row.modality.name}",
            f"Cancelled on {row.cancelled_on:%d/%m/%Y} by {actor.name}. No further installments will be paid, "
            "so no further commission is due on this sale.",
            f"/subscriptions/{row.id}",
        ),
    )


def commission_earned(db: Session, row: Subscription, item: Installment) -> None:
    amount = commission_cents(item.paid_cents or 0, row.commission_bp or 0)
    notify_seller(
        db,
        row.seller,
        Message(
            COMMISSION_EARNED,
            f"Commission earned: {money(amount)} — {row.customer.name}, installment {item.sequence}",
            f"{row.customer.name} paid installment {item.sequence} of {row.modality.name} on {item.paid_on:%d/%m/%Y}: "
            f"{money(item.paid_cents or 0)}.\nYour commission: {format_bp(row.commission_bp or 0)} = {money(amount)}.",
            "/my-commissions",
        ),
    )
    for snapshot in row.overrides:
        override = commission_cents(item.paid_cents or 0, snapshot.rate_bp)
        notify_seller(
            db,
            snapshot.seller,
            Message(
                OVERRIDE_EARNED,
                f"Override earned: {money(override)} — {row.customer.name}, sold by {row.seller.name}",
                f"{row.customer.name} paid installment {item.sequence} of {row.modality.name} on {item.paid_on:%d/%m/%Y}: "
                f"{money(item.paid_cents or 0)}.\nYour override (level {snapshot.level}) on {row.seller.name}'s sales: "
                f"{format_bp(snapshot.rate_bp)} = {money(override)}.",
                "/my-commissions",
            ),
        )


def seller_leader_changed(db: Session, seller: Seller, previous_leader_id: int | None, actor: User) -> None:
    """Both sides of the team are told: the seller about its (new or former)
    leader, the leader(s) about the collaborator joining or leaving."""
    leader = seller.leader
    former = db.get(Seller, previous_leader_id) if previous_leader_id is not None else None
    rates = {item.level: item.rate_bp or 0 for item in seller.region.overrides}
    if leader is not None:
        notify_seller(
            db,
            seller,
            Message(
                SELLER_LEADER_CHANGED,
                f"You now report to {leader.name}",
                f"Set by {actor.name}. {leader.name} coordinates your team in region {seller.region.name}; the region pays "
                f"your leaders an override of {_override_levels(rates)} on your sales, by the company, on top of your commission.",
                "/my-region",
            ),
        )
        notify_seller(
            db,
            leader,
            Message(
                SELLER_LEADER_CHANGED,
                f"{seller.name} joined your team",
                f"Set by {actor.name}. You earn {format_bp(rates.get(1, 0))} on every fee received from "
                f"{seller.name}'s next sales.",
                "/my-team",
            ),
        )
    elif former is not None:
        notify_seller(
            db,
            seller,
            Message(SELLER_LEADER_CHANGED, f"You no longer report to {former.name}", f"Set by {actor.name}.", "/my-region"),
        )
    if former is not None and (leader is None or leader.id != former.id):
        notify_seller(
            db,
            former,
            Message(
                SELLER_LEADER_CHANGED,
                f"{seller.name} left your team",
                f"Set by {actor.name}. The sales {seller.name} already recorded keep paying your override; the next ones do not.",
                "/my-team",
            ),
        )


def _override_levels(rates: dict) -> str:
    """``3% (level 1), 1.5% (level 2)`` or ``nothing``."""
    levels = [f"{format_bp(rate)} (level {level})" for level, rate in sorted(rates.items()) if rate]
    return ", ".join(levels) if levels else "nothing"


def _region_rules(region: Region) -> str:
    plans = ", ".join(
        f"{item.modality.name} ({item.discount_percent}% off)" if item.discount_percent else item.modality.name
        for item in region.plans
    ) or "none"
    overrides = _override_levels({item.level: item.rate_bp or 0 for item in region.overrides})
    return (
        f"Commission: {format_bp(region.commission_bp or 0)} on the plan fees received\n"
        f"Scope: {scope_label(sorted(region.areas, key=lambda area: (area.state, area.city or '')))}\n"
        f"Plans available: {plans}\n"
        f"Team overrides: {overrides}\n\n"
        "These rules apply to your next sales; the ones already recorded keep their terms."
    )


def region_rules_changed(db: Session, region: Region, actor: User) -> None:
    """Every seller of the region is told its new rules."""
    message = Message(
        REGION_UPDATED,
        f"Rules of region {region.name} changed",
        f"Changed by {actor.name}.\n" + _region_rules(region),
        "/my-region",
    )
    for seller in db.query(Seller).filter(Seller.region_id == region.id).all():
        notify_seller(db, seller, message)


def seller_moved(db: Session, seller: Seller, previous_region_name: str, actor: User) -> None:
    notify_seller(
        db,
        seller,
        Message(
            SELLER_MOVED,
            f"You now sell in region {seller.region.name}",
            f"Moved from {previous_region_name} by {actor.name}. Your sales already recorded stay in {previous_region_name}.\n"
            + _region_rules(seller.region),
            "/my-region",
        ),
    )


def seller_status_changed(db: Session, seller: Seller, actor: User) -> None:
    """Deactivated: the seller loses access at once, so only the email
    reaches it (the in-app message waits for a reactivation)."""
    if seller.active:
        message = Message(
            SELLER_STATUS,
            "Your seller access is active again",
            f"Reactivated by {actor.name}. You can sign in and record sales again.",
            "/",
        )
    else:
        message = Message(
            SELLER_STATUS,
            "Your seller access was deactivated",
            f"Deactivated by {actor.name}. You no longer have access to the system; "
            "the commissions still due on your sales will be settled by the company.",
            "/",
        )
    notify_seller(db, seller, message)


# --- a seller's change requests (confirmed by the customer) ------------------------------


def change_summary(change: SaleChange) -> str:
    """``Cancel on 31/10/2026`` or ``Change: plan Plan Bar, expiry 30/06/2027``."""
    if change.kind == "cancel":
        return f"Cancel on {change.cancelled_on:%d/%m/%Y}"
    parts = []
    if change.modality_id is not None:
        parts.append(f"plan {change.modality.name}")
    if change.joined_on is not None:
        parts.append(f"join date {change.joined_on:%d/%m/%Y}")
    if change.expires_on is not None:
        parts.append(f"expiry {change.expires_on:%d/%m/%Y}")
    return "Change: " + ", ".join(parts)


def _change_details(row: Subscription, change: SaleChange) -> str:
    what = "cancel the plan" if change.kind == "cancel" else "change the plan"
    text = (
        f"Seller {change.seller.name} asks to {what} {row.modality.name} of {row.customer.name}.\n"
        f"{change_summary(change)}\n"
        f"Current terms: {row.modality.name}, {row.joined_on:%d/%m/%Y} to {row.expires_on:%d/%m/%Y}"
    )
    if change.note:
        text += f"\nReason: {change.note}"
    return text


def sale_change_requested(db: Session, row: Subscription, change: SaleChange, actor: User) -> None:
    """The customer must confirm (or reject) before anything changes; the
    staff is told too, so it can record the customer's answer given in person."""
    details = _change_details(row, change)
    what = "cancellation" if change.kind == "cancel" else "change"
    notify_customer(
        db,
        row.customer,
        Message(
            SALE_CHANGE_REQUESTED,
            f"Please confirm: {what} of your plan {row.modality.name} asked by {change.seller.name}",
            details + "\n\nNothing changes until you confirm it. Open the plan to confirm or reject the request.",
            f"/subscriptions/{row.id}",
        ),
    )
    notify_staff(
        db,
        Message(
            SALE_CHANGE_REQUESTED,
            f"{change.seller.name} asks a {what} of the plan of {row.customer.name} (awaiting the customer)",
            details + "\n\nThe customer confirms it in its own area, or you record its answer.",
            f"/subscriptions/{row.id}",
        ),
    )


def sale_change_decided(db: Session, row: Subscription, change: SaleChange, actor: User) -> None:
    confirmed = change.status == "confirmed"
    event = SALE_CHANGE_CONFIRMED if confirmed else SALE_CHANGE_REJECTED
    verdict = "confirmed" if confirmed else "rejected"
    by = "the customer" if actor.role == ROLE_CLIENT else f"{actor.name} (staff, on the customer's word)"
    note = f"\nNote: {change.decision_note}" if change.decision_note else ""
    notify_seller(
        db,
        change.seller,
        Message(
            event,
            f"Request {verdict}: {change_summary(change)} — {row.customer.name}",
            f"Your request on the plan {row.modality.name} of {row.customer.name} was {verdict} by {by}.{note}"
            + ("\nThe change is applied." if confirmed else "\nThe plan stays as it was."),
            f"/subscriptions/{row.id}",
        ),
    )
    if actor.role != ROLE_CLIENT:
        notify_customer(
            db,
            row.customer,
            Message(
                event,
                f"Request of {change.seller.name} {verdict} on your behalf: {change_summary(change)}",
                f"Recorded by {actor.name}.{note}" + ("\nThe change is applied." if confirmed else "\nYour plan stays as it was."),
                f"/subscriptions/{row.id}",
            ),
        )


def sale_change_withdrawn(db: Session, row: Subscription, change: SaleChange, actor: User) -> None:
    notify_customer(
        db,
        row.customer,
        Message(
            SALE_CHANGE_WITHDRAWN,
            f"{change.seller.name} withdrew the request: {change_summary(change)}",
            f"The request on your plan {row.modality.name} no longer needs your answer. Your plan stays as it was.",
            f"/subscriptions/{row.id}",
        ),
    )


def subscription_upgraded(db: Session, previous: Subscription, new: Subscription, actor: User) -> None:
    """The customer's plan was replaced: the customer (and the staff, when a
    client or a seller did it) are told what ended and what starts."""
    body = (
        f"Your plan {previous.modality.name} ends on {previous.expires_on:%d/%m/%Y}; "
        f"{new.modality.name} starts on {new.joined_on:%d/%m/%Y}.\n"
        "Installments of the old plan after that date are no longer owed; the ones already paid stay.\n\n"
        + _subscription_details(new)
    )
    notify_customer(db, previous.customer, Message(SUBSCRIPTION_UPGRADED, f"Plan upgraded: {previous.modality.name} → {new.modality.name}", body, f"/subscriptions/{new.id}"))
    if actor.role in (ROLE_CLIENT, ROLE_SELLER):
        notify_staff(
            db,
            Message(
                SUBSCRIPTION_UPGRADED,
                f"{previous.customer.name} upgraded {previous.modality.name} → {new.modality.name} ({actor.name})",
                body,
                f"/subscriptions/{new.id}",
            ),
        )


def subscription_cancelled(db: Session, row: Subscription, actor: User) -> None:
    message = Message(
        SUBSCRIPTION_CANCELLED,
        f"{row.modality.name} cancelled",
        f"Cancelled on {row.cancelled_on:%d/%m/%Y}. Appointments can no longer be scheduled with this plan.",
        f"/subscriptions/{row.id}",
    )
    notify_customer(db, row.customer, message)
    if actor.role == ROLE_CLIENT:
        notify_staff(
            db,
            Message(
                SUBSCRIPTION_CANCELLED,
                f"{row.customer.name} cancelled the plan {row.modality.name}",
                message.body,
                f"/subscriptions/{row.id}",
            ),
        )
    if row.seller is not None:
        sale_cancelled(db, row, actor)


def installment_paid(db: Session, row: Subscription, item: Installment, status: str) -> None:
    body = (
        f"Installment {item.sequence} ({item.period_start:%d/%m/%Y} to {item.period_end:%d/%m/%Y})\n"
        f"Paid on {item.paid_on:%d/%m/%Y}: {money(item.paid_cents or 0)}\n"
        f"Plan status: {status}"
    )
    notify_customer(
        db,
        row.customer,
        Message(INSTALLMENT_PAID, f"Payment received: installment {item.sequence} of {row.modality.name}", body, f"/subscriptions/{row.id}"),
    )
    if row.seller is not None:
        commission_earned(db, row, item)


# --- corporate partnerships (B2B) -----------------------------------------------------


def partner_users(db: Session, partner_id: int) -> list[User]:
    return db.query(User).filter(User.role == ROLE_PARTNER, User.partner_id == partner_id).all()


def partner_emails(db: Session, partner) -> list[str]:
    """The company's contact email plus the addresses of its portal logins."""
    addresses = [partner.email] if partner.email else []
    addresses += [user.email for user in partner_users(db, partner.id)]
    return addresses


def notify_partner(db: Session, partner, message: Message) -> None:
    deliver(db, message, users=partner_users(db, partner.id), emails=partner_emails(db, partner))


def agreement_minimum_changed(db: Session, agreement: Agreement, state, actor: User | None) -> None:
    """The agreement reached its minimum of employees this month, or fell
    below it (the company then pays the discounts of the month): the staff
    and the company are told."""
    who = f" ({actor.name})" if actor is not None else ""
    if state.met:
        title = f"{agreement.partner.name}: minimum of {agreement.min_employees} employees reached"
        body = (
            f"Agreement {agreement.name} bills {state.employees} employees in {state.period} "
            f"(minimum {agreement.min_employees}): no shortfall to pay for the month."
        )
    else:
        title = f"{agreement.partner.name}: below the minimum of {agreement.min_employees} employees"
        body = (
            f"Agreement {agreement.name} bills {state.employees} employees in {state.period}, below the minimum of "
            f"{agreement.min_employees}. The employees keep the negotiated conditions; for every month below the "
            "minimum the company pays the difference (the discounts granted on the month's fees)."
        )
    staff_link = f"/agreements/{agreement.id}"
    notify_staff(db, Message(AGREEMENT_CONDITIONS, title, body + who, staff_link))
    notify_partner(db, agreement.partner, Message(AGREEMENT_CONDITIONS, title, body, f"/my-agreements/{agreement.id}"))


def agreement_invoice_paid(db: Session, agreement: Agreement, period: str, fees_count: int, fees_cents: int, shortfall_cents: int, paid_on, actor: User) -> None:
    """The company's invoice of a month was paid: the company gets the receipt."""
    parts = []
    if fees_count:
        parts.append(f"the company's part of {fees_count} employee fee{'s' if fees_count != 1 else ''}: {money(fees_cents)}")
    if shortfall_cents:
        parts.append(f"shortfall (discounts of a month below the minimum of employees): {money(shortfall_cents)}")
    title = f"Invoice {period} paid — {agreement.partner.name}"
    body = (
        f"Agreement {agreement.name}, invoice {period}: {'; '.join(parts)}. "
        f"Total {money(fees_cents + shortfall_cents)}, paid on {paid_on:%d/%m/%Y}. Registered by {actor.name}."
    )
    notify_partner(db, agreement.partner, Message(AGREEMENT_INVOICE_PAID, title, body, f"/my-agreements/{agreement.id}"))


def share_changed(db: Session, row: Subscription, percent: int, effective_from, actor: User | None) -> None:
    """Co-payment: the split of an employee's fees changed — the employee is
    told what it pays from then on (and the staff, when the company did it)."""
    amount = monthly_amount(row.modality, dependents_count(db, row.customer_id), row.discount_percent or 0)
    company = split_cents(amount, percent)
    partner = row.agreement.partner.name
    split = f"{partner} {percent}% ({money(company)} a month), the employee {100 - percent}% ({money(amount - company)})"
    body = (
        f"From {effective_from:%d/%m/%Y}, {partner} pays {percent}% of your plan {row.modality.name} "
        f"({money(company)} a month) and you pay {100 - percent}% ({money(amount - company)}). "
        "Fees already paid in part keep their split."
    )
    notify_customer(
        db, row.customer, Message(SHARE_CHANGED, f"Your co-payment: you pay {100 - percent}% from {effective_from:%d/%m/%Y}", body, "/my-payments")
    )
    if actor is not None and actor.role == ROLE_PARTNER:
        notify_staff(
            db,
            Message(
                SHARE_CHANGED,
                f"{partner} changed {row.customer.name}'s split: company {percent}%",
                f"From {effective_from:%d/%m/%Y}, {row.customer.name}'s plan {row.modality.name} is paid by {split}. Set by {actor.name} in the company portal.",
                f"/subscriptions/{row.id}",
            ),
        )


def installment_part_paid(db: Session, row: Subscription, item: Installment, part: str) -> None:
    """One part of a shared fee was paid; the fee waits for the other."""
    other = "employee" if part == "company" else "company"
    paid = item.company_paid_cents if part == "company" else item.employee_paid_cents
    who = f"{row.agreement.partner.name} paid its part" if part == "company" else "Your part was paid"
    pending = part_cents(item, other)
    body = (
        f"Installment {item.sequence} of {row.modality.name} ({item.period_start:%d/%m/%Y} to {item.period_end:%d/%m/%Y}): "
        f"{money(paid or 0)} received. "
        + (
            f"Your part, {money(pending)}, is still open (due {item.due_on:%d/%m/%Y})."
            if other == "employee"
            else f"{row.agreement.partner.name}'s part, {money(pending)}, is still open."
        )
    )
    notify_customer(db, row.customer, Message(INSTALLMENT_PART_PAID, f"{who}: installment {item.sequence} of {row.modality.name}", body, "/my-payments"))


def employee_changed_by_company(db: Session, row: Subscription, change: str, actor: User) -> None:
    """The company changed its employee's data, plan or dependents in its
    portal: the employee is told what and by whom (its record is its own)."""
    partner = row.agreement.partner.name
    notify_customer(
        db,
        row.customer,
        Message(
            EMPLOYEE_UPDATED,
            f"{partner} updated your corporate plan",
            f"{change}\nUpdated by {actor.name} ({partner}) in the company portal. "
            "If anything is wrong, talk to your company or to Hollic Health.",
            f"/subscriptions/{row.id}",
        ),
    )


def employee_enrolled_by_company(db: Session, row: Subscription, actor: User) -> None:
    """The company enrolled an employee on its own (portal): the staff is told."""
    notify_staff(
        db,
        Message(
            AGREEMENT_ENROLLMENT,
            f"{row.agreement.partner.name} enrolled {row.customer.name} in {row.modality.name}",
            f"Agreement {row.agreement.name}, {row.joined_on:%d/%m/%Y} to {row.expires_on:%d/%m/%Y}, "
            f"{row.discount_percent}% off the monthly fee ({payer_label(row.agreement.payer)}). Enrolled by {actor.name} in the company portal.",
            f"/subscriptions/{row.id}",
        ),
    )


def employee_removed_by_company(db: Session, row: Subscription, actor: User, withdrawn: bool = False) -> None:
    """The company removed an employee in its portal: the staff is told. A
    ``withdrawn`` enrollment had not started: it was deleted with its fees."""
    if withdrawn:
        body = (
            f"The enrollment starting {row.joined_on:%d/%m/%Y} was withdrawn by {actor.name} in the company portal "
            "before it started: it was removed with its fees."
        )
        link = f"/agreements/{row.agreement_id}"
    else:
        body = (
            f"Enrollment cancelled on {row.cancelled_on:%d/%m/%Y} by {actor.name} in the company portal. "
            "The fees of the periods starting after that date are no longer owed."
        )
        link = f"/subscriptions/{row.id}"
    notify_staff(
        db,
        Message(AGREEMENT_ENROLLMENT, f"{row.agreement.partner.name} removed {row.customer.name} from {row.agreement.name}", body, link),
    )


# --- users --------------------------------------------------------------------------


def user_created(db: Session, user: User) -> None:
    body = f"Hello {user.name}, your access to Hollic Health was created with the email {user.email}."
    if user.customer:
        body += f"\nYou can schedule appointments and manage the plan of {user.customer.name}."
    if user.partner:
        body += (
            f"\nYou manage {user.partner.name}'s corporate plans in the company portal: enroll your employees, "
            "follow your agreements, the minimum of employees and your monthly invoices."
        )
    if user.seller:
        body += (
            f"\nYou sell as {user.seller.name} in region {user.seller.region.name}: register customers, "
            "record your sales and follow your commissions in your area."
        )
    deliver(db, Message(USER_CREATED, "Your access to Hollic Health", body, "/"), users=[user], emails=[user.email])
