from datetime import date, datetime

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from . import clock
from .database import Base


class User(Base):
    """Staff, client, professional, seller or partner login. Roles: admin,
    supervisor, attendant, client, professional, seller, partner. A client
    user is tied to the customer record it may act for; a professional user
    to the professional whose appointments it signs and whose payouts it
    sees; a seller user to the seller whose sales, customers and commissions
    it manages; a partner user to the partner company whose employees and
    agreements it manages in the self-service portal."""

    __tablename__ = "users"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    email: Mapped[str] = mapped_column(String(180), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(200))
    role: Mapped[str] = mapped_column(String(20), default="attendant")
    customer_id: Mapped[int | None] = mapped_column(ForeignKey("customers.id"), nullable=True)
    professional_id: Mapped[int | None] = mapped_column(ForeignKey("professionals.id"), nullable=True)
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id"), nullable=True)
    # Partner company login (self-service portal): the company it manages.
    partner_id: Mapped[int | None] = mapped_column(ForeignKey("partners.id"), nullable=True)
    # Authenticator (TOTP) used by professionals to sign; see app.signing.
    totp_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)
    totp_enabled_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    sign_failures: Mapped[int] = mapped_column(Integer, default=0)
    sign_locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # End-to-end chat identity: the public half of the key pair the browser
    # generated (JWK, ECDH P-256) and its fingerprint; the private key never
    # leaves the user's device (see app.chat).
    chat_public_key: Mapped[str | None] = mapped_column(Text, nullable=True)
    chat_key_fingerprint: Mapped[str | None] = mapped_column(String(64), nullable=True)
    chat_key_updated_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    customer = relationship("Customer")
    professional = relationship("Professional")
    seller = relationship("Seller")
    partner = relationship("Partner")


class AddressMixin:
    """Postal address plus coordinates for the map."""

    street: Mapped[str] = mapped_column(String(160), default="")
    number: Mapped[str] = mapped_column(String(20), default="")
    complement: Mapped[str] = mapped_column(String(80), default="")
    district: Mapped[str] = mapped_column(String(80), default="")
    zip_code: Mapped[str] = mapped_column(String(10), default="")
    latitude: Mapped[float | None] = mapped_column(Float, nullable=True)
    longitude: Mapped[float | None] = mapped_column(Float, nullable=True)


class Clinic(AddressMixin, Base):
    __tablename__ = "clinics"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    city: Mapped[str] = mapped_column(String(80))
    state: Mapped[str] = mapped_column(String(2), default="")
    ibge_code: Mapped[str] = mapped_column(String(7), default="")  # IBGE municipality code
    cnpj: Mapped[str] = mapped_column(String(18), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    directions: Mapped[str] = mapped_column(Text, default="")  # how to get there, parking, floor...
    fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Fee charged for on-call services (see app.pricing); null: same as fee_cents.
    on_call_fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Fee charged on remote appointments (no room is used; may be 0); null: same as fee_cents.
    remote_fee_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Service capacity reserved for Santé Saúde (the clinic also serves
    # outside the network): bookings stop at these counts. 0: unlimited.
    # See app.capacity.
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    monthly_limit: Mapped[int] = mapped_column(Integer, default=0)


class Company(AddressMixin, Base):
    """Head office of the discount network: jurisdiction used for taxes on
    plan fees and header of the accounting reports. Single row (id 1)."""

    __tablename__ = "company"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), default="Santé Saúde")
    cnpj: Mapped[str] = mapped_column(String(18), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(2), default="")
    ibge_code: Mapped[str] = mapped_column(String(7), default="")
    tax_regime: Mapped[str] = mapped_column(String(40), default="")  # e.g. Lucro Presumido
    municipal_registration: Mapped[str] = mapped_column(String(30), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")


class TaxRule(Base):
    """A tax rate and where it applies. For each tax ``code`` the most
    specific active rule matching the service wins (see app.taxes)."""

    __tablename__ = "tax_rules"

    id: Mapped[int] = mapped_column(primary_key=True)
    code: Mapped[str] = mapped_column(String(20), index=True)  # ISS, PIS, COFINS...
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[str] = mapped_column(String(10))  # federal | state | municipal
    rate_bp: Mapped[int] = mapped_column(Integer)  # basis points: 1 bp = 0.01 %
    applies_to: Mapped[str] = mapped_column(String(15), default="all")  # appointment | subscription | all
    base: Mapped[str] = mapped_column(String(10), default="amount")  # amount (billed) | gross
    # Scope filters; null means "any".
    state: Mapped[str | None] = mapped_column(String(2), nullable=True)
    city: Mapped[str | None] = mapped_column(String(80), nullable=True)
    clinic_id: Mapped[int | None] = mapped_column(ForeignKey("clinics.id"), nullable=True)
    specialty_id: Mapped[int | None] = mapped_column(ForeignKey("specialties.id"), nullable=True)
    modality_id: Mapped[int | None] = mapped_column(ForeignKey("modalities.id"), nullable=True)
    effective_from: Mapped[date] = mapped_column(Date)
    effective_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    clinic = relationship("Clinic")
    specialty = relationship("Specialty")
    modality = relationship("Modality")


class Specialty(Base):
    __tablename__ = "specialties"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Price for on-call services; null: same as price_cents.
    on_call_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Price for remote appointments; null: same as price_cents.
    remote_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Professional(Base):
    __tablename__ = "professionals"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    # CPF: the ICP-Brasil certificate used to sign prescriptions must carry it (app.prescriptions).
    document: Mapped[str] = mapped_column(String(20), default="")
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id"))
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"))
    price_cents: Mapped[int] = mapped_column(Integer, default=0)
    slot_minutes: Mapped[int] = mapped_column(Integer, default=30)
    # On-call availability: the professional also takes on-call shifts
    # (``WorkingHours`` rows with ``on_call``), paid at a higher rate.
    on_call_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    on_call_price_cents: Mapped[int] = mapped_column(Integer, default=0)  # > price_cents when enabled
    # Ad-hoc urgent visits: the professional flags this in its own profile;
    # the rate is at least three times the regular fee (app.pricing). Never
    # accepted during an on-call shift (app.scheduling).
    urgent_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    urgent_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Remote appointments (video call): offered only when the professional
    # says so (own profile or the professional form), booked in the regular
    # working hours like in-person ones and paid at the remote rate
    # (null: the regular fee). See app.remote and app.pricing.
    remote_enabled: Mapped[bool] = mapped_column(Boolean, default=False)
    remote_price_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    # Service capacity reserved for Santé Saúde (the professional also has
    # appointments outside the network): bookings stop at these counts.
    # 0: unlimited. See app.capacity.
    daily_limit: Mapped[int] = mapped_column(Integer, default=0)
    monthly_limit: Mapped[int] = mapped_column(Integer, default=0)

    specialty = relationship("Specialty")
    clinic = relationship("Clinic")
    working_hours = relationship(
        "WorkingHours",
        cascade="all, delete-orphan",
        order_by="(WorkingHours.weekday, WorkingHours.start_time)",
    )
    time_off = relationship(
        "TimeOff",
        cascade="all, delete-orphan",
        order_by="(TimeOff.date, TimeOff.start_time)",
    )


class WorkingHours(Base):
    """A recurring weekly range (HH:mm) in which the professional takes
    appointments: regular hours, or an **on-call** shift (``on_call``) whose
    appointments are priced at the on-call rates."""

    __tablename__ = "working_hours"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)
    start_time: Mapped[str] = mapped_column(String(5))
    end_time: Mapped[str] = mapped_column(String(5))
    on_call: Mapped[bool] = mapped_column(Boolean, default=False)


class TimeOff(Base):
    """A date (whole day, or a HH:mm range) the professional does not want to work."""

    __tablename__ = "time_off"

    id: Mapped[int] = mapped_column(primary_key=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id"), index=True)
    date: Mapped[date] = mapped_column(Date)
    start_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    end_time: Mapped[str | None] = mapped_column(String(5), nullable=True)
    reason: Mapped[str] = mapped_column(String(200), default="")


class PersonMixin:
    """Patient record fields shared by the holder and their dependents."""

    name: Mapped[str] = mapped_column(String(120))
    birth_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    sex: Mapped[str] = mapped_column(String(1), default="")
    document: Mapped[str] = mapped_column(String(20), default="")
    identity_card: Mapped[str] = mapped_column(String(30), default="")
    health_card: Mapped[str] = mapped_column(String(20), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    email: Mapped[str] = mapped_column(String(180), default="")
    mother_name: Mapped[str] = mapped_column(String(120), default="")
    father_name: Mapped[str] = mapped_column(String(120), default="")
    street: Mapped[str] = mapped_column(String(160), default="")
    number: Mapped[str] = mapped_column(String(20), default="")
    complement: Mapped[str] = mapped_column(String(80), default="")
    district: Mapped[str] = mapped_column(String(80), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(2), default="")
    zip_code: Mapped[str] = mapped_column(String(10), default="")
    blood_type: Mapped[str] = mapped_column(String(3), default="")
    allergies: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # Secret of the virtual card (base32); the rotating code comes from it.
    # Never leaves the server: the card endpoint returns the code, not this.
    card_secret: Mapped[str | None] = mapped_column(String(64), nullable=True)


class Customer(PersonMixin, Base):
    __tablename__ = "customers"

    id: Mapped[int] = mapped_column(primary_key=True)
    # The seller who registered the customer (null: the staff did). With the
    # customers it sold a plan to, this is the seller's portfolio (app.auth).
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id"), nullable=True, index=True)

    seller = relationship("Seller")
    dependents = relationship(
        "Dependent",
        cascade="all, delete-orphan",
        order_by="Dependent.name",
    )


class Dependent(PersonMixin, Base):
    __tablename__ = "dependents"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    kinship: Mapped[str] = mapped_column(String(30), default="")


class PlanCategory(Base):
    """A category of plans (e.g. Individual, Family, Enterprise). An
    **enterprise** category's plans are only available through company
    agreements — never sold to individuals nor by sellers. A category may set
    the **waiting period** of its plans (``grace_days``; null: each plan's
    own). See app.categories."""

    __tablename__ = "plan_categories"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(80), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    enterprise: Mapped[bool] = mapped_column(Boolean, default=False)
    grace_days: Mapped[int | None] = mapped_column(Integer, nullable=True)


class Modality(Base):
    """A plan. ``discount_percent`` applies to every specialty unless a
    ``PlanDiscount`` row overrides it for a specific specialty.

    ``appointment_limit`` is how many appointments the plan covers (with its
    discount) per ``appointment_limit_period``: ``month`` (each monthly
    billing period of the subscription) or ``subscription`` (the whole
    period). 0 means unlimited. Appointments booked over the limit are still
    allowed but pay the full price (see app.usage).

    A plan may belong to a ``category``: an enterprise category makes it an
    **enterprise plan** (only through company agreements), and a category's
    waiting period replaces the plan's own ``grace_days`` (app.categories)."""

    __tablename__ = "modalities"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    plan_value_cents: Mapped[int] = mapped_column(Integer, default=0)
    dependent_limit: Mapped[int] = mapped_column(Integer, default=0)
    grace_days: Mapped[int] = mapped_column(Integer, default=0)
    extra_dependent_cents: Mapped[int] = mapped_column(Integer, default=0)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    appointment_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0: unlimited
    appointment_limit_period: Mapped[str] = mapped_column(String(15), default="month")  # month | subscription
    category_id: Mapped[int | None] = mapped_column(ForeignKey("plan_categories.id"), nullable=True, index=True)

    discounts = relationship("PlanDiscount", cascade="all, delete-orphan")
    category = relationship("PlanCategory", lazy="joined")


class PlanDiscount(Base):
    __tablename__ = "plan_discounts"
    __table_args__ = (UniqueConstraint("modality_id", "specialty_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    modality_id: Mapped[int] = mapped_column(ForeignKey("modalities.id"), index=True)
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id"))
    percent: Mapped[int] = mapped_column(Integer, default=0)

    specialty = relationship("Specialty")


class Region(Base):
    """A sales region: the sellers grouped in it share its rules (see
    app.sales). ``commission_bp`` is the sellers' commission on the plan fees
    received from their sales (basis points: 1 bp = 0.01 %). The **scope**
    (``areas``) is where its sellers may sell (the customer's address), and
    ``plans`` the plans they may sell there, each with the region's special
    discount on the monthly fee."""

    __tablename__ = "regions"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    description: Mapped[str] = mapped_column(Text, default="")
    commission_bp: Mapped[int] = mapped_column(Integer, default=0)

    areas = relationship(
        "RegionArea", cascade="all, delete-orphan", order_by="(RegionArea.state, RegionArea.city)"
    )
    plans = relationship("RegionPlan", cascade="all, delete-orphan", order_by="RegionPlan.id")
    # Team overrides by level above the seller (see Seller): set by the company per region.
    overrides = relationship("RegionOverride", cascade="all, delete-orphan", order_by="RegionOverride.level")


class RegionOverride(Base):
    """The override a region pays to the seller's ancestors in its team:
    ``level`` 1 is the direct leader, 2 the leader's leader, and so on."""

    __tablename__ = "region_overrides"
    __table_args__ = (UniqueConstraint("region_id", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    level: Mapped[int] = mapped_column(Integer)
    rate_bp: Mapped[int] = mapped_column(Integer, default=0)


class RegionArea(Base):
    """Part of a region's scope: a whole state (``city`` empty) or one
    municipality of a state."""

    __tablename__ = "region_areas"
    __table_args__ = (UniqueConstraint("region_id", "state", "city"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    state: Mapped[str] = mapped_column(String(2))
    city: Mapped[str] = mapped_column(String(80), default="")  # "": the whole state


class RegionPlan(Base):
    """A plan the region's sellers may sell, with the region's special
    discount on its monthly fee (0: list price)."""

    __tablename__ = "region_plans"
    __table_args__ = (UniqueConstraint("region_id", "modality_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    modality_id: Mapped[int] = mapped_column(ForeignKey("modalities.id"), index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)

    modality = relationship("Modality")


class Seller(Base):
    """A salesperson. Always belongs to one region, whose rules bound what it
    sells; an inactive seller keeps its history but cannot sell.

    Sellers are organised in **teams** of any depth: a seller with a
    ``leader`` reports to it (a coordinator of the same region), who may
    report to a regional manager, and so on. Every ancestor earns the
    override its region sets for its level above the seller
    (``RegionOverride``) on the fees received from the sale; the ancestors
    and their rates are snapshotted on each sale (``SaleOverride``)."""

    __tablename__ = "sellers"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    document: Mapped[str] = mapped_column(String(20), default="")  # CPF / CNPJ
    email: Mapped[str] = mapped_column(String(180), default="")
    phone: Mapped[str] = mapped_column(String(30), default="")
    region_id: Mapped[int] = mapped_column(ForeignKey("regions.id"), index=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Team: the seller this one reports to (same region; no cycles).
    leader_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id"), nullable=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )

    region = relationship("Region")
    leader = relationship("Seller", remote_side="Seller.id", foreign_keys=[leader_id])


class Partner(Base):
    """A partner company (B2B): plans are negotiated directly with it, for
    its employees, through six-month ``Agreement`` (contracts). The company
    may pay its employees' plans (consolidated monthly invoice) or let each
    employee pay at the negotiated price. Its logins (role ``partner``)
    manage its employees in the self-service portal. See app.partners."""

    __tablename__ = "partners"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(160), unique=True)  # legal / trade name
    cnpj: Mapped[str] = mapped_column(String(18), default="")
    contact_name: Mapped[str] = mapped_column(String(120), default="")  # HR / benefits contact
    email: Mapped[str] = mapped_column(String(180), default="")  # receives the agreement's notices and invoices
    phone: Mapped[str] = mapped_column(String(30), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    state: Mapped[str] = mapped_column(String(2), default="")
    notes: Mapped[str] = mapped_column(Text, default="")
    # An inactive partner accepts no new enrollments in any of its agreements.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )

    agreements = relationship("Agreement", back_populates="partner", order_by="Agreement.starts_on.desc()")


class Agreement(Base):
    """A corporate agreement (contract) with a partner company, for **six
    months** (``ends_on`` = ``starts_on`` + 6 months - 1 day): the plans its
    employees may be enrolled in, each with negotiated conditions
    (``AgreementPlan``), granted **in exchange of a minimum number of
    employees** (``min_employees``).

    The terms are **frozen**: the conditions are snapshotted on every
    enrollment and the agreement's terms cannot change once employees are
    enrolled — a renegotiation is a new contract. The employees always get
    the negotiated conditions; for every month the company falls below its
    minimum, it pays Santé Saúde the difference (the discounts granted on
    that month's fees — ``AgreementCharge``). See app.partners.

    ``payer``: ``company`` (the partner pays every fee: one consolidated
    invoice per month), ``shared`` (**co-payment**: each fee is split between
    the company and the employee — ``company_share_percent`` by default, or
    the plan's own — and the split of each employee may change over time, as
    they agree: ``EnrollmentShare``) or ``employee`` (each employee pays its
    own fees at the negotiated price; the company's invoice only carries the
    shortfall)."""

    __tablename__ = "agreements"

    id: Mapped[int] = mapped_column(primary_key=True)
    partner_id: Mapped[int] = mapped_column(ForeignKey("partners.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))  # e.g. "Acme 2026/2"
    starts_on: Mapped[date] = mapped_column(Date)
    ends_on: Mapped[date] = mapped_column(Date)  # derived: six months
    min_employees: Mapped[int] = mapped_column(Integer, default=1)
    payer: Mapped[str] = mapped_column(String(10), default="company")  # company | shared | employee
    # The company's part of each fee: 100 (company), 0 (employee) or, for a
    # co-payment, the default split of new enrollments (1-99; a plan may set its own).
    company_share_percent: Mapped[int] = mapped_column(Integer, default=100)
    # Suspended (false): no new enrollments; the ones in force keep running.
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    notes: Mapped[str] = mapped_column(Text, default="")
    # Last known state of the minimum in the current month (to tell the partner when it changes).
    conditions_met: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )

    partner = relationship("Partner", back_populates="agreements")
    plans = relationship("AgreementPlan", cascade="all, delete-orphan", order_by="AgreementPlan.id")
    charges = relationship("AgreementCharge", cascade="all, delete-orphan", order_by="AgreementCharge.period")
    created_by = relationship("User")


class AgreementPlan(Base):
    """A plan covered by an agreement and its negotiated conditions: the
    discount on the monthly fee, optionally a waiting period of its own
    (``grace_days``; null: the plan's; 0: none) and, in a co-payment, the
    company's default part of its fees (``company_share_percent``; null: the
    agreement's)."""

    __tablename__ = "agreement_plans"
    __table_args__ = (UniqueConstraint("agreement_id", "modality_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), index=True)
    modality_id: Mapped[int] = mapped_column(ForeignKey("modalities.id"), index=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    grace_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_share_percent: Mapped[int | None] = mapped_column(Integer, nullable=True)

    modality = relationship("Modality")


class EnrollmentShare(Base):
    """The co-payment split of one employee's fees from ``effective_from`` on:
    the company's part (``company_share_percent``), the rest the employee's.
    The company and its employee agree it — and may change it over time: the
    row in force on the first day of a fee's period splits that fee, unless a
    part of it was already paid (then it keeps its split). The first row is
    the enrollment's (from the join day)."""

    __tablename__ = "enrollment_shares"
    __table_args__ = (UniqueConstraint("subscription_id", "effective_from"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    company_share_percent: Mapped[int] = mapped_column(Integer)
    effective_from: Mapped[date] = mapped_column(Date)
    note: Mapped[str] = mapped_column(String(200), default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )

    created_by = relationship("User")


class AgreementCharge(Base):
    """The **shortfall** the partner company paid for one month of an
    agreement: the month's fees (those due in it) were billed to fewer
    employees than the agreement's minimum, so the company pays Santé Saúde
    the discounts granted on them. Recorded when paid (the amount and the
    headcount are snapshotted then), with its tax lines."""

    __tablename__ = "agreement_charges"
    __table_args__ = (UniqueConstraint("agreement_id", "period"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    agreement_id: Mapped[int] = mapped_column(ForeignKey("agreements.id"), index=True)
    period: Mapped[str] = mapped_column(String(7))  # YYYY-MM
    employees: Mapped[int] = mapped_column(Integer, default=0)  # billed that month
    min_employees: Mapped[int] = mapped_column(Integer, default=0)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)  # the discounts of the month
    paid_on: Mapped[date] = mapped_column(Date)
    registered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    registered_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)

    registered_by = relationship("User")
    taxes = relationship("AgreementChargeTax", cascade="all, delete-orphan", order_by="AgreementChargeTax.id")


class Subscription(Base):
    """A customer's plan for a period. Status is not stored: it is derived from
    the dates and the installments (see app.billing.subscription_status).

    A subscription sold by a seller snapshots the seller's region, its
    commission and its special discount for the plan when the sale is made
    (see app.sales): later changes to the region never rewrite a sale.

    A subscription with an ``agreement`` is a **corporate enrollment**: the
    customer is an employee of the agreement's partner company, the plan is
    one the agreement covers and the negotiated discount is snapshotted on it
    (see app.partners). It is never a seller's sale."""

    __tablename__ = "subscriptions"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"), index=True)
    modality_id: Mapped[int] = mapped_column(ForeignKey("modalities.id"))
    joined_on: Mapped[date] = mapped_column(Date)
    expires_on: Mapped[date] = mapped_column(Date)
    cancelled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    # Sale by a seller (null: sold directly by the staff or by the client).
    seller_id: Mapped[int | None] = mapped_column(ForeignKey("sellers.id"), nullable=True, index=True)
    region_id: Mapped[int | None] = mapped_column(ForeignKey("regions.id"), nullable=True, index=True)
    commission_bp: Mapped[int] = mapped_column(Integer, default=0)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)  # special discount on the monthly fee
    # Corporate enrollment (B2B): the agreement of the employee's company (null: a B2C plan);
    # ``discount_percent`` then snapshots the agreement's negotiated discount.
    agreement_id: Mapped[int | None] = mapped_column(ForeignKey("agreements.id"), nullable=True, index=True)
    # Plan upgrade: the subscription this one replaced / was replaced by.
    # The replaced one ends the day before the new one starts.
    upgraded_from_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True)
    upgraded_to_id: Mapped[int | None] = mapped_column(ForeignKey("subscriptions.id"), nullable=True, index=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )

    customer = relationship("Customer")
    modality = relationship("Modality")
    seller = relationship("Seller", foreign_keys=[seller_id])
    region = relationship("Region")
    agreement = relationship("Agreement")
    created_by = relationship("User")
    upgraded_from = relationship("Subscription", remote_side="Subscription.id", foreign_keys=[upgraded_from_id])
    upgraded_to = relationship("Subscription", remote_side="Subscription.id", foreign_keys=[upgraded_to_id])
    # The seller's ancestors when the sale was made, with the override each earns on it.
    overrides = relationship("SaleOverride", cascade="all, delete-orphan", order_by="SaleOverride.level")
    # Corporate enrollment: how its fees are split between the company and the employee, over time.
    shares = relationship("EnrollmentShare", cascade="all, delete-orphan", order_by="EnrollmentShare.effective_from")
    installments = relationship(
        "Installment",
        cascade="all, delete-orphan",
        order_by="Installment.sequence",
    )


class SaleOverride(Base):
    """Snapshot of one ancestor of the selling seller at the time of the sale
    (``level`` 1 = direct leader) and the region's override rate for that
    level: what the ancestor earns on every fee received from the sale."""

    __tablename__ = "sale_overrides"
    __table_args__ = (UniqueConstraint("subscription_id", "level"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    level: Mapped[int] = mapped_column(Integer)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id"), index=True)
    rate_bp: Mapped[int] = mapped_column(Integer, default=0)

    seller = relationship("Seller")


class SaleChange(Base):
    """A change a seller asks on one of its sales — an **edit** (plan and/or
    period) or a **cancellation** — that the customer must confirm before it
    is applied (the client login, or the staff on the client's word). One
    pending request per subscription at a time; the seller may withdraw it.
    The request stores what was asked; the subscription only changes when
    confirmed (then the same rules as a staff edit/cancel apply)."""

    __tablename__ = "sale_changes"

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    seller_id: Mapped[int] = mapped_column(ForeignKey("sellers.id"), index=True)
    requested_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    kind: Mapped[str] = mapped_column(String(10))  # edit | cancel
    # Edit: the new terms; cancel: the cancellation date.
    modality_id: Mapped[int | None] = mapped_column(ForeignKey("modalities.id"), nullable=True)
    joined_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    expires_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    cancelled_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    note: Mapped[str] = mapped_column(Text, default="")  # the seller's reason, shown to the customer
    status: Mapped[str] = mapped_column(String(10), default="pending", index=True)  # pending | confirmed | rejected | withdrawn
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    decided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    decided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    decision_note: Mapped[str] = mapped_column(Text, default="")

    seller = relationship("Seller")
    modality = relationship("Modality")
    requested_by = relationship("User", foreign_keys=[requested_by_id])
    decided_by = relationship("User", foreign_keys=[decided_by_id])


class Installment(Base):
    """One monthly payment of a subscription.

    A corporate enrollment's fee has **two parts**: the company's
    (``company_share_cents``, paid through the company's invoice) and the
    employee's (the rest), each registered on its own (``company_paid_on`` /
    ``employee_paid_on``; a part of 0 needs no payment). The fee is paid —
    ``paid_on`` (the later part) and ``paid_cents`` (both parts) — once both
    are in: status, coverage and taxes follow it. Other fees are paid in one go."""

    __tablename__ = "installments"
    __table_args__ = (UniqueConstraint("subscription_id", "sequence"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    subscription_id: Mapped[int] = mapped_column(ForeignKey("subscriptions.id"), index=True)
    sequence: Mapped[int] = mapped_column(Integer)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    due_on: Mapped[date] = mapped_column(Date)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Special discount included in amount_cents (list - amount): the seller's
    # region's, or the corporate agreement's.
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    registered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Corporate enrollments: the company's part of amount_cents and the payment of each part.
    company_share_cents: Mapped[int] = mapped_column(Integer, default=0)
    company_paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    company_paid_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    company_registered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    employee_paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    employee_paid_cents: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_registered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)

    registered_by = relationship("User", foreign_keys=[registered_by_id])
    taxes = relationship("InstallmentTax", cascade="all, delete-orphan", order_by="InstallmentTax.id")


class Appointment(Base):
    __tablename__ = "appointments"

    id: Mapped[int] = mapped_column(primary_key=True)
    customer_id: Mapped[int] = mapped_column(ForeignKey("customers.id"))
    dependent_id: Mapped[int | None] = mapped_column(ForeignKey("dependents.id"), nullable=True)
    professional_id: Mapped[int] = mapped_column(ForeignKey("professionals.id"))
    specialty_id: Mapped[int] = mapped_column(ForeignKey("specialties.id"))
    clinic_id: Mapped[int] = mapped_column(ForeignKey("clinics.id"))
    marked_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    status: Mapped[str] = mapped_column(String(20), default="agendada")
    # Price snapshot taken when the appointment is booked. ``on_call``: the
    # slot is an on-call shift of the professional, so the three components
    # below are the on-call values (see app.pricing.price_components).
    on_call: Mapped[bool] = mapped_column(Boolean, default=False)
    # ``urgent``: an ad-hoc urgent visit, outside the agenda, at the
    # professional's urgent rate (never inside an on-call shift).
    urgent: Mapped[bool] = mapped_column(Boolean, default=False)
    # ``remote``: a remote appointment (video call) instead of an in-person
    # one: booked in a regular slot, priced with the remote price list, held
    # at ``meeting_url`` (issued at booking; the staff may set its own link).
    remote: Mapped[bool] = mapped_column(Boolean, default=False)
    meeting_url: Mapped[str] = mapped_column(String(500), default="")
    fee_cents: Mapped[int] = mapped_column(Integer, default=0)
    specialty_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    professional_price_cents: Mapped[int] = mapped_column(Integer, default=0)
    gross_cents: Mapped[int] = mapped_column(Integer, default=0)
    modality_id: Mapped[int | None] = mapped_column(ForeignKey("modalities.id"), nullable=True)
    discount_percent: Mapped[int] = mapped_column(Integer, default=0)
    discount_cents: Mapped[int] = mapped_column(Integer, default=0)
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    # Plan limit snapshot: the plan's appointment allowance when this was
    # booked and whether it was already used up (then no discount: full price).
    over_limit: Mapped[bool] = mapped_column(Boolean, default=False)
    plan_limit: Mapped[int] = mapped_column(Integer, default=0)  # 0: unlimited
    plan_limit_period: Mapped[str] = mapped_column(String(15), default="")
    # Taxes included in the amount (company liability) and what is left.
    tax_cents: Mapped[int] = mapped_column(Integer, default=0)
    net_cents: Mapped[int] = mapped_column(Integer, default=0)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    # Reminders already sent for this slot (reset when the appointment moves).
    reminded_day_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    reminded_hour_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Attendance confirmation (audit trail): arrival and end of the visit.
    checked_in_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_in_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_in_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    checked_in_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    checked_out_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    checked_out_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    checked_out_lat: Mapped[float | None] = mapped_column(Float, nullable=True)
    checked_out_lng: Mapped[float | None] = mapped_column(Float, nullable=True)
    # Start of the visit: the patient proved who they are with the virtual
    # card's code (see app.virtual_card). Required before the signature.
    started_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    started_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    start_failures: Mapped[int] = mapped_column(Integer, default=0)
    start_locked_until: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # The professional's signature is what completes the appointment
    # (status ``realizada``): nobody else can mark it as done.
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    signature_image: Mapped[str | None] = mapped_column(Text, nullable=True)  # JPEG data URL drawn by the professional
    # Settlements that paid this appointment's fees (professional / clinic share).
    professional_settlement_id: Mapped[int | None] = mapped_column(ForeignKey("payout_settlements.id"), nullable=True)
    clinic_settlement_id: Mapped[int | None] = mapped_column(ForeignKey("payout_settlements.id"), nullable=True)

    customer = relationship("Customer")
    dependent = relationship("Dependent")
    professional = relationship("Professional")
    specialty = relationship("Specialty")
    clinic = relationship("Clinic")
    modality = relationship("Modality")
    created_by = relationship("User", foreign_keys=[created_by_id])
    checked_in_by = relationship("User", foreign_keys=[checked_in_by_id])
    checked_out_by = relationship("User", foreign_keys=[checked_out_by_id])
    started_by = relationship("User", foreign_keys=[started_by_id])
    signed_by = relationship("User", foreign_keys=[signed_by_id])
    taxes = relationship("AppointmentTax", cascade="all, delete-orphan", order_by="AppointmentTax.id")
    # Medical prescriptions attached to the visit (see app.prescriptions).
    documents = relationship("AppointmentDocument", cascade="all, delete-orphan", order_by="AppointmentDocument.id")


class AppointmentDocument(Base):
    """A document attached to an appointment — today only medical
    **prescriptions** (``kind``). The content lives in the prescriptions
    storage under ``storage_key``; the row keeps the original name, the type
    detected from the content, the size, a SHA-256 fingerprint and who
    attached it. Rows go with the appointment (cancelled or deleted) and are
    purged at the end of the legal retention (see app.prescriptions).

    A prescription is valid for pharmacies once the appointment's
    professional **signs** it with its ICP-Brasil certificate: the detached
    CAdES signature (``.p7s``) is stored under ``signature_key`` and the
    signer's data snapshotted here; the signature is re-verifiable at any
    time from the two files (see app.cms)."""

    __tablename__ = "appointment_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20), default="prescription", index=True)
    filename: Mapped[str] = mapped_column(String(160))
    content_type: Mapped[str] = mapped_column(String(80))
    size_bytes: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    note: Mapped[str] = mapped_column(String(200), default="")
    uploaded_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    # Digital signature (ICP-Brasil, CAdES detached), by the appointment's professional.
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    signature_key: Mapped[str | None] = mapped_column(String(200), nullable=True)  # the .p7s in the storage
    signature_sha256: Mapped[str] = mapped_column(String(64), default="")
    signer_name: Mapped[str] = mapped_column(String(160), default="")  # certificate subject CN
    signer_document: Mapped[str] = mapped_column(String(20), default="")  # CPF from the certificate
    signer_certificate: Mapped[str | None] = mapped_column(Text, nullable=True)  # PEM, public part
    certificate_issuer: Mapped[str] = mapped_column(String(160), default="")
    certificate_serial: Mapped[str] = mapped_column(String(64), default="")
    certificate_not_after: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    signature_policy: Mapped[str] = mapped_column(String(80), default="")  # policy OID, when one was referenced

    uploaded_by = relationship("User", foreign_keys=[uploaded_by_id])
    signed_by = relationship("User", foreign_keys=[signed_by_id])


class Conversation(Base):
    """The end-to-end encrypted chat of a remote appointment, between the
    patient (the customer's client logins) and the appointment's
    professional. The server stores ciphertext only: the conversation key is
    generated in a participant's browser and reaches the others wrapped for
    their public keys (``ConversationKey``). See app.chat."""

    __tablename__ = "conversations"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    # New-message notifications are throttled per side (patient / professional).
    customer_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    professional_notified_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    appointment = relationship("Appointment")
    keys = relationship("ConversationKey", cascade="all, delete-orphan", order_by="ConversationKey.id")
    messages = relationship("ChatMessage", cascade="all, delete-orphan", order_by="ChatMessage.id")
    attachments = relationship("ChatAttachment", cascade="all, delete-orphan", order_by="ChatAttachment.id")
    reads = relationship("ConversationRead", cascade="all, delete-orphan")


class ConversationKey(Base):
    """The conversation key wrapped for one participant's identity key
    (``recipient_fingerprint``): an ephemeral ECDH public key, an IV and the
    AES-GCM ciphertext of the raw key. Opaque to the server."""

    __tablename__ = "conversation_keys"
    __table_args__ = (UniqueConstraint("conversation_id", "recipient_fingerprint"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    recipient_fingerprint: Mapped[str] = mapped_column(String(64))
    ephemeral_public_key: Mapped[str] = mapped_column(Text)  # JWK
    iv: Mapped[str] = mapped_column(String(40))  # base64
    wrapped_key: Mapped[str] = mapped_column(Text)  # base64
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )


class ChatMessage(Base):
    """One message: AES-GCM ciphertext of a JSON payload (text and the
    attachments' names, types, sizes and IVs) the server cannot read."""

    __tablename__ = "chat_messages"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    sender_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    ciphertext: Mapped[str] = mapped_column(Text)  # base64
    iv: Mapped[str] = mapped_column(String(40))  # base64
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0), index=True
    )

    sender = relationship("User")
    attachments = relationship("ChatAttachment", order_by="ChatAttachment.id")


class ChatAttachment(Base):
    """An encrypted blob in the chat storage: uploaded first, then referenced
    by a message (until then it is an orphan, purged by the worker)."""

    __tablename__ = "chat_attachments"

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    message_id: Mapped[int | None] = mapped_column(ForeignKey("chat_messages.id"), nullable=True, index=True)
    uploaded_by_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    storage_key: Mapped[str] = mapped_column(String(200), unique=True)
    size_bytes: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )


class ConversationRead(Base):
    """How far a participant has read."""

    __tablename__ = "conversation_reads"
    __table_args__ = (UniqueConstraint("conversation_id", "user_id"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    conversation_id: Mapped[int] = mapped_column(ForeignKey("conversations.id"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    last_read_message_id: Mapped[int] = mapped_column(Integer, default=0)
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class TaxLineMixin:
    """Snapshot of one tax applied to a service."""

    code: Mapped[str] = mapped_column(String(20))
    name: Mapped[str] = mapped_column(String(120))
    level: Mapped[str] = mapped_column(String(10))
    rate_bp: Mapped[int] = mapped_column(Integer)
    base_cents: Mapped[int] = mapped_column(Integer)
    amount_cents: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(2), default="")
    city: Mapped[str] = mapped_column(String(80), default="")
    ibge_code: Mapped[str] = mapped_column(String(7), default="")


class AppointmentTax(TaxLineMixin, Base):
    __tablename__ = "appointment_taxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    appointment_id: Mapped[int] = mapped_column(ForeignKey("appointments.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("tax_rules.id"), nullable=True)


class AgreementChargeTax(TaxLineMixin, Base):
    __tablename__ = "agreement_charge_taxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    charge_id: Mapped[int] = mapped_column(ForeignKey("agreement_charges.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("tax_rules.id"), nullable=True)


class InstallmentTax(TaxLineMixin, Base):
    __tablename__ = "installment_taxes"

    id: Mapped[int] = mapped_column(primary_key=True)
    installment_id: Mapped[int] = mapped_column(ForeignKey("installments.id"), index=True)
    rule_id: Mapped[int | None] = mapped_column(ForeignKey("tax_rules.id"), nullable=True)


class Notification(Base):
    """In-app message for one user. Created in the same transaction as the
    event it reports, so it is never lost."""

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), index=True)
    event: Mapped[str] = mapped_column(String(60), index=True)
    title: Mapped[str] = mapped_column(String(200))
    body: Mapped[str] = mapped_column(Text, default="")
    link: Mapped[str] = mapped_column(String(200), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0), index=True
    )
    read_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class QueueJob(Base):
    """Outbox job (e.g. an email). Written with the event, delivered by the
    worker; failures are retried with back-off until ``max_attempts``."""

    __tablename__ = "queue_jobs"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30), index=True)
    payload: Mapped[str] = mapped_column(Text)  # JSON
    status: Mapped[str] = mapped_column(String(20), default="pending", index=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_at: Mapped[datetime] = mapped_column(DateTime, index=True)
    last_error: Mapped[str] = mapped_column(Text, default="")
    event: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)


class PayoutClosing(Base):
    """One monthly closing: on the closing day the worker settles every payee
    up to the end of the previous month (one job per payee). Unique per
    period, so a closing is never run twice."""

    __tablename__ = "payout_closings"

    id: Mapped[int] = mapped_column(primary_key=True)
    period: Mapped[str] = mapped_column(String(7), unique=True)  # YYYY-MM
    period_end: Mapped[date] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(15), default="scheduled")  # scheduled | dispatched | done
    payees_total: Mapped[int] = mapped_column(Integer, default=0)
    settlements_created: Mapped[int] = mapped_column(Integer, default=0)
    payees_skipped: Mapped[int] = mapped_column(Integer, default=0)  # nothing to settle
    triggered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)  # null: automatic
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )
    dispatched_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)

    triggered_by = relationship("User")


class PayoutSettlement(Base):
    """Closing of a payee's account: every signed appointment not yet settled
    up to ``period_end`` is gathered, outstanding advances are deducted and
    the net is split into installments (``PayoutPayment`` rows)."""

    __tablename__ = "payout_settlements"

    id: Mapped[int] = mapped_column(primary_key=True)
    closing_id: Mapped[int | None] = mapped_column(ForeignKey("payout_closings.id"), nullable=True, index=True)
    payee_type: Mapped[str] = mapped_column(String(15))  # professional | clinic
    professional_id: Mapped[int | None] = mapped_column(ForeignKey("professionals.id"), nullable=True, index=True)
    clinic_id: Mapped[int | None] = mapped_column(ForeignKey("clinics.id"), nullable=True, index=True)
    period_start: Mapped[date] = mapped_column(Date)
    period_end: Mapped[date] = mapped_column(Date)
    appointments_count: Mapped[int] = mapped_column(Integer, default=0)
    earned_cents: Mapped[int] = mapped_column(Integer, default=0)
    advances_cents: Mapped[int] = mapped_column(Integer, default=0)  # deducted here
    net_cents: Mapped[int] = mapped_column(Integer, default=0)
    installments_count: Mapped[int] = mapped_column(Integer, default=1)
    notes: Mapped[str] = mapped_column(Text, default="")
    voided_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    voided_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, default=lambda: clock.now().replace(microsecond=0)
    )

    professional = relationship("Professional")
    clinic = relationship("Clinic")
    created_by = relationship("User", foreign_keys=[created_by_id])
    voided_by = relationship("User", foreign_keys=[voided_by_id])
    payments = relationship(
        "PayoutPayment",
        foreign_keys="PayoutPayment.settlement_id",
        cascade="all, delete-orphan",
        order_by="PayoutPayment.sequence",
    )
    applications = relationship("AdvanceApplication", cascade="all, delete-orphan", order_by="AdvanceApplication.id")


class PayoutPayment(Base):
    """Money handed to a payee: an **advance** (ahead of any settlement) or an
    **installment** of a settlement. Once paid it carries a receipt number;
    the professional signs the receipt to confirm the money was received
    (a clinic's receipt is acknowledged by the staff with who received it)."""

    __tablename__ = "payout_payments"

    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(15))  # advance | installment
    payee_type: Mapped[str] = mapped_column(String(15))  # professional | clinic
    professional_id: Mapped[int | None] = mapped_column(ForeignKey("professionals.id"), nullable=True, index=True)
    clinic_id: Mapped[int | None] = mapped_column(ForeignKey("clinics.id"), nullable=True, index=True)
    settlement_id: Mapped[int | None] = mapped_column(ForeignKey("payout_settlements.id"), nullable=True, index=True)
    sequence: Mapped[int] = mapped_column(Integer, default=1)  # installment k of N
    amount_cents: Mapped[int] = mapped_column(Integer, default=0)
    due_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    paid_on: Mapped[date | None] = mapped_column(Date, nullable=True)
    method: Mapped[str] = mapped_column(String(20), default="")  # pix | transfer | cash | check | other
    reference: Mapped[str] = mapped_column(String(120), default="")  # bank reference, cheque number...
    notes: Mapped[str] = mapped_column(Text, default="")
    applied_cents: Mapped[int] = mapped_column(Integer, default=0)  # advances: deducted so far
    receipt_number: Mapped[str | None] = mapped_column(String(20), unique=True, nullable=True)
    registered_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    registered_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    # Confirmation that the money was received.
    signed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # professional's login
    signed_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    signature_image: Mapped[str | None] = mapped_column(Text, nullable=True)  # JPEG data URL drawn by the professional
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)  # clinic: staff records
    acknowledged_by_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"), nullable=True)
    received_by: Mapped[str] = mapped_column(String(120), default="")  # clinic: who received, and
    received_by_document: Mapped[str] = mapped_column(String(30), default="")  # their document

    professional = relationship("Professional")
    clinic = relationship("Clinic")
    settlement = relationship("PayoutSettlement", foreign_keys=[settlement_id], overlaps="payments")
    registered_by = relationship("User", foreign_keys=[registered_by_id])
    signed_by = relationship("User", foreign_keys=[signed_by_id])
    acknowledged_by = relationship("User", foreign_keys=[acknowledged_by_id])


class AdvanceApplication(Base):
    """How much of an advance a settlement deducted."""

    __tablename__ = "payout_advance_applications"

    id: Mapped[int] = mapped_column(primary_key=True)
    settlement_id: Mapped[int] = mapped_column(ForeignKey("payout_settlements.id"), index=True)
    advance_id: Mapped[int] = mapped_column(ForeignKey("payout_payments.id"), index=True)
    amount_cents: Mapped[int] = mapped_column(Integer)

    advance = relationship("PayoutPayment", foreign_keys=[advance_id])
