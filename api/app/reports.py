"""Weekly and monthly management reports.

Pure aggregations over appointment, subscription and installment rows; the
router loads the rows and applies the role rules. Periods are ISO weeks
(Monday to Sunday, key ``YYYY-Www``) or calendar months (key ``YYYY-MM``).

Amounts in the appointments report sum the *booked* appointments (scheduled
and completed); cancelled ones are counted but do not add to the balance.
Booked appointments over the plan's appointment limit (billed at the full
price, see app.usage) are counted apart with what they bill.

Seller commissions are paid on the plan fees received (installments, by
payment date) from the subscriptions each seller sold, at the commission
snapshotted on the sale; a team leader's override likewise, on the sales of
its collaborators.

Professional payouts sum only *completed* appointments: each one pays the
professional's fee snapshotted at booking time (``professional_price_cents``).
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from typing import Iterable

from .billing import STATUS_ACTIVE, STATUS_OVERDUE, STATUS_PENDING, receipts, subscription_status
from .sales import commission_cents
from .scheduling import occupies_slot

WEEK = "week"
MONTH = "month"
GRANULARITIES = (WEEK, MONTH)


@dataclass(frozen=True)
class Bucket:
    key: str
    start: date
    end: date


def week_bucket(day: date) -> Bucket:
    start = day - timedelta(days=day.weekday())
    iso_year, iso_week, _ = day.isocalendar()
    return Bucket(f"{iso_year}-W{iso_week:02d}", start, start + timedelta(days=6))


def month_bucket(day: date) -> Bucket:
    start = day.replace(day=1)
    next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return Bucket(f"{day.year}-{day.month:02d}", start, next_month - timedelta(days=1))


def bucket_of(day: date, granularity: str) -> Bucket:
    if granularity == WEEK:
        return week_bucket(day)
    if granularity == MONTH:
        return month_bucket(day)
    raise ValueError(f"Unknown granularity: {granularity}")


def buckets_between(start: date, end: date, granularity: str) -> list[Bucket]:
    """Every bucket touching [start, end], in order, so empty periods still show."""
    result = []
    current = bucket_of(start, granularity)
    while current.start <= end:
        result.append(current)
        current = bucket_of(current.end + timedelta(days=1), granularity)
    return result


def default_range(granularity: str, today: date) -> tuple[date, date]:
    """Last 8 weeks or last 6 months, up to today."""
    if granularity == WEEK:
        return week_bucket(today - timedelta(weeks=7)).start, today
    return month_bucket(today.replace(day=1) - timedelta(days=150)).start, today


def as_date(value: datetime | date) -> date:
    return value.date() if isinstance(value, datetime) else value


# --- appointments ---------------------------------------------------------------


@dataclass
class Amounts:
    scheduled: int = 0
    completed: int = 0
    cancelled: int = 0
    gross_cents: int = 0
    discount_cents: int = 0
    amount_cents: int = 0
    over_limit: int = 0  # booked over the plan limit: full price
    over_limit_cents: int = 0
    on_call: int = 0  # booked on-call services (on-call price list)
    on_call_cents: int = 0
    urgent: int = 0  # booked ad-hoc urgent visits (urgent rate)
    urgent_cents: int = 0
    remote: int = 0  # booked remote appointments (remote price list)
    remote_cents: int = 0

    @property
    def appointments(self) -> int:
        return self.scheduled + self.completed + self.cancelled

    def add(self, row) -> None:
        if row.status == "realizada":
            self.completed += 1
        elif row.status == "cancelada":
            self.cancelled += 1
        else:
            self.scheduled += 1
        if occupies_slot(row.status):
            self.gross_cents += row.gross_cents
            self.discount_cents += row.discount_cents
            self.amount_cents += row.amount_cents
            if row.over_limit:
                self.over_limit += 1
                self.over_limit_cents += row.amount_cents
            if row.on_call:
                self.on_call += 1
                self.on_call_cents += row.amount_cents
            if getattr(row, "urgent", False):
                self.urgent += 1
                self.urgent_cents += row.amount_cents
            if getattr(row, "remote", False):
                self.remote += 1
                self.remote_cents += row.amount_cents


def appointments_by_period(rows: Iterable, start: date, end: date, granularity: str) -> list[tuple[Bucket, Amounts]]:
    totals: dict[str, Amounts] = defaultdict(Amounts)
    for row in rows:
        totals[bucket_of(as_date(row.marked_at), granularity).key].add(row)
    return [(bucket, totals[bucket.key]) for bucket in buckets_between(start, end, granularity)]


def appointments_breakdown(rows: Iterable, key, name) -> list[tuple[int, str, Amounts]]:
    """Group rows by ``key(row)`` -> (id, label) and sort by amount, then name."""
    groups: dict[tuple[int, str], Amounts] = defaultdict(Amounts)
    for row in rows:
        groups[(key(row), name(row))].add(row)
    return sorted(
        ((item_id, label, amounts) for (item_id, label), amounts in groups.items()),
        key=lambda item: (-item[2].amount_cents, item[1]),
    )


# --- attendants -----------------------------------------------------------------


@dataclass
class AttendantStats:
    user_id: int | None
    name: str
    role: str | None
    appointments_booked: int = 0
    appointments_completed: int = 0
    appointments_cancelled: int = 0
    plans_sold: int = 0
    payments_registered: int = 0
    payments_cents: int = 0

    @property
    def completion_rate(self) -> float:
        decided = self.appointments_completed + self.appointments_cancelled
        return round(self.appointments_completed / decided, 4) if decided else 0.0


def _actor(user) -> tuple[int | None, str, str | None]:
    if user is None:
        return None, "System", None
    return user.id, user.name, user.role


def attendant_stats(appointments: Iterable, subscriptions: Iterable, installments: Iterable) -> list[AttendantStats]:
    """Counts per user who booked appointments, sold plans or registered payments."""
    stats: dict[int | None, AttendantStats] = {}

    def get(user) -> AttendantStats:
        user_id, name, role = _actor(user)
        if user_id not in stats:
            stats[user_id] = AttendantStats(user_id, name, role)
        return stats[user_id]

    for row in appointments:
        item = get(row.created_by)
        item.appointments_booked += 1
        if row.status == "realizada":
            item.appointments_completed += 1
        elif row.status == "cancelada":
            item.appointments_cancelled += 1
    for row in subscriptions:
        get(row.created_by).plans_sold += 1
    for row in installments:
        item = get(row.registered_by)
        item.payments_registered += 1
        item.payments_cents += row.paid_cents or 0
    return sorted(stats.values(), key=lambda item: (-item.appointments_booked, -item.plans_sold, item.name))


def attendants_by_period(
    appointments: Iterable, subscriptions: Iterable, installments: Iterable, start: date, end: date, granularity: str
) -> list[tuple[Bucket, list[AttendantStats]]]:
    appointments, subscriptions, installments = list(appointments), list(subscriptions), list(installments)
    result = []
    for bucket in buckets_between(start, end, granularity):
        inside = lambda day: bucket.start <= day <= bucket.end  # noqa: E731
        result.append(
            (
                bucket,
                attendant_stats(
                    [row for row in appointments if inside(as_date(row.created_at))],
                    [row for row in subscriptions if inside(as_date(row.created_at))],
                    [row for row in installments if row.paid_on and inside(row.paid_on)],
                ),
            )
        )
    return result


# --- professional payouts ---------------------------------------------------------


PAYABLE_STATUS = "realizada"


def payout_cents(row) -> int:
    """What the professional earns for one appointment: the professional's fee
    snapshotted at booking time. Only completed appointments — those the
    professional signed — are paid; the plan discount is borne by the
    network, not by the professional."""
    return row.professional_price_cents if row.status == PAYABLE_STATUS else 0


@dataclass
class PayoutStats:
    completed: int = 0
    scheduled: int = 0
    cancelled: int = 0
    payout_cents: int = 0
    on_call: int = 0  # completed on-call services, paid at the on-call rate
    on_call_cents: int = 0
    urgent: int = 0  # completed urgent visits, paid at the urgent rate
    urgent_cents: int = 0
    remote: int = 0  # completed remote appointments, paid at the remote rate
    remote_cents: int = 0

    def add(self, row) -> None:
        if row.status == PAYABLE_STATUS:
            self.completed += 1
            self.payout_cents += payout_cents(row)
            if getattr(row, "on_call", False):
                self.on_call += 1
                self.on_call_cents += payout_cents(row)
            if getattr(row, "urgent", False):
                self.urgent += 1
                self.urgent_cents += payout_cents(row)
            if getattr(row, "remote", False):
                self.remote += 1
                self.remote_cents += payout_cents(row)
        elif row.status == "cancelada":
            self.cancelled += 1
        else:
            self.scheduled += 1


@dataclass
class ProfessionalPayout:
    professional: object
    totals: PayoutStats = field(default_factory=PayoutStats)
    periods: list = field(default_factory=list)  # [(Bucket, PayoutStats)]


def payout_stats(rows: Iterable) -> PayoutStats:
    stats = PayoutStats()
    for row in rows:
        stats.add(row)
    return stats


def payouts_by_period(rows: Iterable, start: date, end: date, granularity: str) -> list[tuple[Bucket, PayoutStats]]:
    totals: dict[str, PayoutStats] = defaultdict(PayoutStats)
    for row in rows:
        totals[bucket_of(as_date(row.marked_at), granularity).key].add(row)
    return [(bucket, totals[bucket.key]) for bucket in buckets_between(start, end, granularity)]


def payouts_by_professional(
    professionals: Iterable, rows: Iterable, start: date, end: date, granularity: str
) -> list[ProfessionalPayout]:
    """One entry per professional (also those with nothing due, so the control
    is complete), highest payout first, then by name."""
    by_professional: dict[int, list] = defaultdict(list)
    for row in rows:
        by_professional[row.professional_id].append(row)
    result = []
    for professional in professionals:
        own = by_professional.get(professional.id, [])
        result.append(
            ProfessionalPayout(
                professional=professional,
                totals=payout_stats(own),
                periods=payouts_by_period(own, start, end, granularity),
            )
        )
    return sorted(result, key=lambda item: (-item.totals.payout_cents, -item.totals.completed, item.professional.name))


def matches_professional(professional, name: str | None, specialty_id: int | None, clinic_id: int | None) -> bool:
    """Case-insensitive name substring plus exact specialty/clinic filters."""
    if name and name.strip().lower() not in professional.name.lower():
        return False
    if specialty_id is not None and professional.specialty_id != specialty_id:
        return False
    if clinic_id is not None and professional.clinic_id != clinic_id:
        return False
    return True


# --- plans ----------------------------------------------------------------------


@dataclass
class PlanStats:
    sold: int = 0
    cancelled: int = 0
    payments: int = 0
    revenue_cents: int = 0
    # Booked appointments priced with the plan in the range (by service date):
    # inside its appointment limit (discounted) or over it (full price).
    appointments_covered: int = 0
    appointments_over_limit: int = 0
    over_limit_cents: int = 0


def plan_stats(subscriptions: Iterable, installments: Iterable, start: date, end: date, appointments: Iterable = ()) -> PlanStats:
    stats = PlanStats()
    for row in subscriptions:
        if start <= as_date(row.created_at) <= end:
            stats.sold += 1
        if row.cancelled_on is not None and start <= row.cancelled_on <= end:
            stats.cancelled += 1
    for row in installments:
        # Money received by date: each part of a corporate fee on its own payment date.
        for day, cents in receipts(row):
            if start <= day <= end:
                stats.payments += 1
                stats.revenue_cents += cents
    for row in appointments:
        if row.modality_id is None or not occupies_slot(row.status) or not (start <= as_date(row.marked_at) <= end):
            continue
        if row.over_limit:
            stats.appointments_over_limit += 1
            stats.over_limit_cents += row.amount_cents
        else:
            stats.appointments_covered += 1
    return stats


def plans_by_period(
    subscriptions: Iterable, installments: Iterable, start: date, end: date, granularity: str, appointments: Iterable = ()
) -> list[tuple[Bucket, PlanStats]]:
    subscriptions, installments, appointments = list(subscriptions), list(installments), list(appointments)
    return [
        (bucket, plan_stats(subscriptions, installments, bucket.start, bucket.end, appointments))
        for bucket in buckets_between(start, end, granularity)
    ]


def plans_breakdown(
    subscriptions: Iterable, installments: Iterable, start: date, end: date, appointments: Iterable = ()
) -> list[tuple[int, str, PlanStats]]:
    by_plan: dict[tuple[int, str], list] = defaultdict(list)
    for row in subscriptions:
        by_plan[(row.modality_id, row.modality.name)].append(row)
    appointments_by_plan: dict[int, list] = defaultdict(list)
    for row in appointments:
        if row.modality_id is not None:
            appointments_by_plan[row.modality_id].append(row)
            if row.modality is not None and (row.modality_id, row.modality.name) not in by_plan:
                by_plan[(row.modality_id, row.modality.name)] = []  # plan with appointments but no subscription rows
    installments = list(installments)
    result = []
    for (modality_id, name), rows in by_plan.items():
        ids = {row.id for row in rows}
        stats = plan_stats(
            rows, [item for item in installments if item.subscription_id in ids], start, end, appointments_by_plan.get(modality_id, [])
        )
        result.append((modality_id, name, stats))
    return sorted(result, key=lambda item: (-item[2].revenue_cents, -item[2].sold, item[1]))


@dataclass
class AgreementStats:
    """Corporate enrollments of an agreement in a range (see app.partners)."""

    enrolled: int = 0  # by creation date
    cancelled: int = 0  # by cancellation date
    payments: int = 0  # fees paid, by payment date
    revenue_cents: int = 0
    discount_cents: int = 0  # negotiated discount on the fees paid
    company_paid_cents: int = 0  # received from the company (its part of the fees)
    employee_paid_cents: int = 0  # received from the employees (their parts)


def agreement_stats(subscriptions: Iterable, start: date, end: date) -> AgreementStats:
    stats = AgreementStats()
    for row in subscriptions:
        if start <= as_date(row.created_at) <= end:
            stats.enrolled += 1
        if row.cancelled_on is not None and start <= row.cancelled_on <= end:
            stats.cancelled += 1
        for item in row.installments:
            if item.paid_on is not None and start <= item.paid_on <= end:
                stats.discount_cents += item.discount_cents or 0  # of the fees settled in the range
            for day, cents in receipts(item):
                if start <= day <= end:
                    stats.payments += 1
                    stats.revenue_cents += cents
            if item.company_paid_on is not None and start <= item.company_paid_on <= end:
                stats.company_paid_cents += item.company_paid_cents or 0
            if item.employee_paid_on is not None and start <= item.employee_paid_on <= end:
                stats.employee_paid_cents += item.employee_paid_cents or 0
    return stats


# --- seller commissions -----------------------------------------------------------


@dataclass
class CommissionStats:
    """Sales made by sellers and their commission (see app.sales), plus the
    team side: the sales of collaborators and the leaders' override on the
    fees they received."""

    sold: int = 0  # by sale date (created_at)
    cancelled: int = 0  # by cancellation date
    payments: int = 0  # installments paid, by payment date
    revenue_cents: int = 0
    discount_cents: int = 0  # region's special discount on the fees paid
    commission_cents: int = 0
    team_sold: int = 0
    team_payments: int = 0
    team_revenue_cents: int = 0
    override_cents: int = 0
    leader_override_cents: int = 0  # what the own sales paid to the seller's ancestors (all levels)

    @property
    def total_cents(self) -> int:
        return self.commission_cents + self.override_cents


def commission_stats(subscriptions: Iterable, start: date, end: date, seller_id: int | None = None) -> CommissionStats:
    """``subscriptions`` sold by sellers, with their installments; each paid
    installment pays the commission snapshotted on its subscription to its
    seller and the override snapshotted on it to the seller's leader.

    With ``seller_id``: the commission side counts that seller's own sales
    and the team side the sales below it, at the override snapshotted for
    its level (so the stats are *that seller's* earnings). Without it: every
    sale on both sides, every level (the company's cost)."""
    stats = CommissionStats()
    for row in subscriptions:
        if row.seller_id is None:
            continue
        own = seller_id is None or row.seller_id == seller_id
        # The override rows this seller (or, without one, every ancestor) earns on the sale.
        overrides = [item for item in row.overrides if seller_id is None or item.seller_id == seller_id]
        led = bool(overrides)
        if own and start <= as_date(row.created_at) <= end:
            stats.sold += 1
        if own and row.cancelled_on is not None and start <= row.cancelled_on <= end:
            stats.cancelled += 1
        if led and start <= as_date(row.created_at) <= end:
            stats.team_sold += 1
        for item in row.installments:
            if item.paid_on is None or not (start <= item.paid_on <= end):
                continue
            if own:
                stats.payments += 1
                stats.revenue_cents += item.paid_cents or 0
                stats.discount_cents += item.discount_cents or 0
                stats.commission_cents += commission_cents(item.paid_cents or 0, row.commission_bp or 0)
                stats.leader_override_cents += sum(commission_cents(item.paid_cents or 0, snap.rate_bp) for snap in row.overrides)
            if led:
                stats.team_payments += 1
                stats.team_revenue_cents += item.paid_cents or 0
                stats.override_cents += sum(commission_cents(item.paid_cents or 0, snap.rate_bp) for snap in overrides)
    return stats


def commissions_by_period(
    subscriptions: Iterable, start: date, end: date, granularity: str, seller_id: int | None = None
) -> list[tuple[Bucket, CommissionStats]]:
    subscriptions = list(subscriptions)
    return [
        (bucket, commission_stats(subscriptions, bucket.start, bucket.end, seller_id))
        for bucket in buckets_between(start, end, granularity)
    ]


def commissions_breakdown(subscriptions: Iterable, key, start: date, end: date) -> dict:
    """key(subscription) -> CommissionStats of the sales grouped under it
    (both sides: the group is e.g. a region, not a seller)."""
    groups: dict = defaultdict(list)
    for row in subscriptions:
        groups[key(row)].append(row)
    return {group: commission_stats(rows, start, end) for group, rows in groups.items()}


@dataclass
class SubscriptionCounts:
    active: int = 0
    overdue: int = 0
    pending: int = 0


def subscription_counts(subscriptions: Iterable, today: date) -> SubscriptionCounts:
    counts = SubscriptionCounts()
    for row in subscriptions:
        status = subscription_status(row, row.installments, today)
        if status == STATUS_ACTIVE:
            counts.active += 1
        elif status == STATUS_OVERDUE:
            counts.overdue += 1
        elif status == STATUS_PENDING:
            counts.pending += 1
    return counts


# --- taxes ----------------------------------------------------------------------


@dataclass
class TaxTotals:
    services: int = 0
    amount_cents: int = 0
    tax_cents: int = 0
    by_code: dict[str, "TaxGroup"] = field(default_factory=dict)

    @property
    def net_cents(self) -> int:
        return self.amount_cents - self.tax_cents


@dataclass
class TaxGroup:
    key: str
    label: str
    services: int = 0
    base_cents: int = 0
    tax_cents: int = 0


@dataclass(frozen=True)
class TaxedService:
    """A completed appointment or a paid installment with its tax lines."""

    source: str  # appointment | subscription
    on: date
    amount_cents: int
    lines: tuple


def group_lines(services: Iterable[TaxedService], key, label) -> list[TaxGroup]:
    groups: dict[str, TaxGroup] = {}
    for service in services:
        touched: set[str] = set()
        for line in service.lines:
            k = key(line, service)
            group = groups.setdefault(k, TaxGroup(k, label(line, service)))
            group.base_cents += line.base_cents
            group.tax_cents += line.amount_cents
            if k not in touched:
                group.services += 1
                touched.add(k)
    return sorted(groups.values(), key=lambda g: (-g.tax_cents, g.label))


def tax_totals(services: Iterable[TaxedService]) -> TaxTotals:
    totals = TaxTotals()
    services = list(services)
    for service in services:
        totals.services += 1
        totals.amount_cents += service.amount_cents
        totals.tax_cents += sum(line.amount_cents for line in service.lines)
    totals.by_code = {g.key: g for g in group_lines(services, lambda l, s: l.code, lambda l, s: l.name)}
    return totals


def taxes_by_period(services: Iterable[TaxedService], start: date, end: date, granularity: str) -> list[tuple[Bucket, TaxTotals]]:
    services = list(services)
    result = []
    for bucket in buckets_between(start, end, granularity):
        inside = [s for s in services if bucket.start <= s.on <= bucket.end]
        result.append((bucket, tax_totals(inside)))
    return result


def jurisdiction_key(line, service) -> str:
    if line.level == "federal":
        return "federal"
    if line.level == "state":
        return f"state:{line.state or '?'}"
    return f"city:{line.state or '?'}:{line.city or '?'}:{line.ibge_code or ''}"


def jurisdiction_label(line, service) -> str:
    if line.level == "federal":
        return "Federal"
    if line.level == "state":
        return f"State {line.state or '?'}"
    ibge = f" (IBGE {line.ibge_code})" if line.ibge_code else ""
    return f"{line.city or '?'} / {line.state or '?'}{ibge}"


# --- attendance -----------------------------------------------------------------


@dataclass
class AttendanceStats:
    scheduled: int = 0
    checked_in: int = 0
    checked_out: int = 0
    completed_without_checkin: int = 0
    no_shows: int = 0
    awaiting: int = 0
    cancelled: int = 0
    arrival_minutes: list = field(default_factory=list)
    visit_minutes: list = field(default_factory=list)

    @property
    def attendance_rate(self) -> float:
        decided = self.checked_in + self.completed_without_checkin + self.no_shows
        return round((self.checked_in + self.completed_without_checkin) / decided, 4) if decided else 0.0

    @property
    def avg_arrival_minutes(self) -> float | None:
        return round(sum(self.arrival_minutes) / len(self.arrival_minutes), 1) if self.arrival_minutes else None

    @property
    def avg_visit_minutes(self) -> float | None:
        return round(sum(self.visit_minutes) / len(self.visit_minutes), 1) if self.visit_minutes else None


def attendance_stats(rows: Iterable, now: datetime) -> AttendanceStats:
    from .attendance import attendance_state

    stats = AttendanceStats()
    for row in rows:
        stats.scheduled += 1
        state = attendance_state(row, now)
        if state == "cancelled":
            stats.cancelled += 1
        elif state == "no_show":
            stats.no_shows += 1
        elif state == "awaiting":
            stats.awaiting += 1
        elif row.checked_in_at is None:  # signed without any arrival record
            stats.completed_without_checkin += 1
        else:  # checked_in, in_progress, checked_out, or completed after a check-in
            stats.checked_in += 1
            stats.arrival_minutes.append((row.checked_in_at - row.marked_at).total_seconds() / 60)
            if row.checked_out_at is not None:
                stats.checked_out += 1
                stats.visit_minutes.append((row.checked_out_at - row.checked_in_at).total_seconds() / 60)
    return stats


def attendance_by_period(rows: Iterable, start: date, end: date, granularity: str, now: datetime) -> list[tuple[Bucket, AttendanceStats]]:
    rows = list(rows)
    return [
        (bucket, attendance_stats([r for r in rows if bucket.start <= as_date(r.marked_at) <= bucket.end], now))
        for bucket in buckets_between(start, end, granularity)
    ]


def attendance_breakdown(rows: Iterable, key, label, now: datetime) -> list[tuple[str, AttendanceStats]]:
    groups: dict[str, list] = defaultdict(list)
    for row in rows:
        groups[label(row)].append(row)
    return sorted(((name, attendance_stats(items, now)) for name, items in groups.items()), key=lambda item: (-item[1].scheduled, item[0]))
