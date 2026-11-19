import re
from datetime import date, datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator

from . import clock
from .pricing import urgent_minimum
from .remote import clean_meeting_url
from .sales import normalize_place
from .scheduling import normalize_marked_at

TIME_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
APPOINTMENT_STATUSES = {"agendada", "realizada", "cancelada"}


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"


class LoginIn(BaseModel):
    email: EmailStr
    password: str


ROLES = {"admin", "supervisor", "attendant", "client", "professional", "seller", "partner"}


class UserOut(BaseModel):
    id: int
    name: str
    email: EmailStr
    role: str
    customer_id: int | None = None
    customer_name: str | None = None
    professional_id: int | None = None
    professional_name: str | None = None
    seller_id: int | None = None
    seller_name: str | None = None
    seller_team_size: int = 0  # sellers: how many collaborators it leads (the UI shows "My team")
    partner_id: int | None = None  # partner company logins: the company of the portal
    partner_name: str | None = None
    totp_enabled: bool = False  # professionals: authenticator confirmed (needed to sign)


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    role: str
    password: str | None = Field(default=None, min_length=6, max_length=200)
    customer_id: int | None = None  # required for clients, forbidden otherwise
    professional_id: int | None = None  # required for professionals, forbidden otherwise
    seller_id: int | None = None  # required for sellers, forbidden otherwise
    partner_id: int | None = None  # required for partner company logins, forbidden otherwise

    @field_validator("role")
    @classmethod
    def _role(cls, value: str) -> str:
        if value not in ROLES:
            raise ValueError("role must be admin, supervisor, attendant, client, professional, seller or partner")
        return value

    @model_validator(mode="after")
    def _links(self):
        if self.role == "client" and self.customer_id is None:
            raise ValueError("client users must be linked to a customer")
        if self.role != "client" and self.customer_id is not None:
            raise ValueError("only client users are linked to a customer")
        if self.role == "professional" and self.professional_id is None:
            raise ValueError("professional users must be linked to a professional")
        if self.role != "professional" and self.professional_id is not None:
            raise ValueError("only professional users are linked to a professional")
        if self.role == "seller" and self.seller_id is None:
            raise ValueError("seller users must be linked to a seller")
        if self.role != "seller" and self.seller_id is not None:
            raise ValueError("only seller users are linked to a seller")
        if self.role == "partner" and self.partner_id is None:
            raise ValueError("partner users must be linked to a partner company")
        if self.role != "partner" and self.partner_id is not None:
            raise ValueError("only partner users are linked to a partner company")
        return self


class PasswordChangeIn(BaseModel):
    """Any login changes its own password by proving the current one."""

    current_password: str
    new_password: str = Field(min_length=6, max_length=200)

    @model_validator(mode="after")
    def _different(self):
        if self.new_password == self.current_password:
            raise ValueError("the new password must differ from the current one")
        return self


class AddressIn(BaseModel):
    street: str = Field(default="", max_length=160)
    number: str = Field(default="", max_length=20)
    complement: str = Field(default="", max_length=80)
    district: str = Field(default="", max_length=80)
    zip_code: str = Field(default="", max_length=10)
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)

    @model_validator(mode="after")
    def _coordinates_pair(self):
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be informed together")
        return self


class AddressOut(BaseModel):
    """Read-side helpers derived from the address fields."""

    address: str
    maps_url: str
    osm_url: str


class ClinicIn(AddressIn):
    name: str = Field(min_length=1, max_length=120)
    city: str = Field(min_length=1, max_length=80)
    state: str = Field(default="", max_length=2)
    ibge_code: str = Field(default="", max_length=7)
    cnpj: str = Field(default="", max_length=18)
    phone: str = Field(default="", max_length=30)
    directions: str = ""
    fee_cents: int = Field(ge=0, default=0)
    # Fee for on-call services; null = same as fee_cents. Never below it.
    on_call_fee_cents: int | None = Field(default=None, ge=0)
    # Fee on remote appointments (no room is used: may be 0); null = same as fee_cents.
    remote_fee_cents: int | None = Field(default=None, ge=0)
    # Capacity reserved for Hollic Health (0 = unlimited): bookings stop here.
    daily_limit: int = Field(ge=0, default=0)
    monthly_limit: int = Field(ge=0, default=0)

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        return value.strip().upper()

    @model_validator(mode="after")
    def _on_call_fee(self):
        if self.on_call_fee_cents is not None and self.on_call_fee_cents < self.fee_cents:
            raise ValueError("on_call_fee_cents cannot be below the regular fee")
        return self

    @field_validator("ibge_code")
    @classmethod
    def _ibge(cls, value: str) -> str:
        value = value.strip()
        if value and (not value.isdigit() or len(value) != 7):
            raise ValueError("ibge_code must have 7 digits")
        return value


class ClinicOut(ClinicIn, AddressOut):
    id: int


class SpecialtyIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    price_cents: int = Field(ge=0, default=0)
    # Price for on-call services; null = same as price_cents. Never below it.
    on_call_price_cents: int | None = Field(default=None, ge=0)
    # Price on remote appointments; null = same as price_cents.
    remote_price_cents: int | None = Field(default=None, ge=0)

    @model_validator(mode="after")
    def _on_call_price(self):
        if self.on_call_price_cents is not None and self.on_call_price_cents < self.price_cents:
            raise ValueError("on_call_price_cents cannot be below the regular price")
        return self


class SpecialtyOut(SpecialtyIn):
    id: int

    model_config = {"from_attributes": True}


class WorkingHoursIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str
    end: str

    @field_validator("start", "end")
    @classmethod
    def _hh_mm(cls, value: str) -> str:
        value = value.strip()
        if TIME_RE.match(value) is None:
            raise ValueError(f"Invalid time: {value}")
        return value

    @model_validator(mode="after")
    def _ordered(self):
        if self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class WorkingHoursOut(WorkingHoursIn):
    pass


class WorkingHoursPut(BaseModel):
    ranges: list[WorkingHoursIn]  # regular hours
    on_call_ranges: list[WorkingHoursIn] | None = None  # None keeps the current on-call shifts


class ProfessionalIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    # CPF (digits or formatted); the ICP-Brasil certificate that signs prescriptions must carry it.
    document: str = Field(default="", max_length=20)
    specialty_id: int
    clinic_id: int
    price_cents: int = Field(ge=0, default=0)
    slot_minutes: int = Field(ge=5, le=240, default=30)
    # On-call availability: the professional takes on-call shifts
    # (``on_call_hours``) paid at ``on_call_price_cents``, which must be
    # greater than the regular fee.
    on_call_enabled: bool = False
    on_call_price_cents: int = Field(ge=0, default=0)
    # Ad-hoc urgent visits (the professional also flags this in its own
    # profile): the rate is at least three times the regular fee.
    urgent_enabled: bool = False
    urgent_price_cents: int = Field(ge=0, default=0)
    # Remote appointments (video call), offered only when enabled (the
    # professional also flags this in its own profile); paid at the remote
    # rate, null = the regular fee.
    remote_enabled: bool = False
    remote_price_cents: int | None = Field(default=None, ge=0)
    # Capacity reserved for Hollic Health (0 = unlimited): the professional also
    # has appointments outside the network, so bookings stop at these counts.
    daily_limit: int = Field(ge=0, default=0)
    monthly_limit: int = Field(ge=0, default=0)
    # None keeps the current hours on update and applies the default week on create.
    working_hours: list[WorkingHoursIn] | None = None
    # On-call shifts (weekly ranges); None keeps the current ones on update.
    # Only with on_call_enabled; disabling on-call removes them.
    on_call_hours: list[WorkingHoursIn] | None = None

    @model_validator(mode="after")
    def _on_call(self):
        if self.on_call_enabled and self.on_call_price_cents <= self.price_cents:
            raise ValueError("on_call_price_cents must be greater than the regular fee (price_cents)")
        if not self.on_call_enabled and self.on_call_hours:
            raise ValueError("on_call_hours require on_call_enabled")
        if self.urgent_enabled and self.urgent_price_cents < urgent_minimum(self.price_cents):
            raise ValueError("urgent_price_cents must be at least three times the regular fee (price_cents)")
        return self


class UrgentAvailabilityIn(BaseModel):
    """What a professional sets in its own profile: whether it takes ad-hoc
    urgent visits and at which rate (at least three times its regular fee;
    omitted when enabling: the current rate, or that minimum)."""

    urgent_enabled: bool
    urgent_price_cents: int | None = Field(default=None, ge=0)


class RemoteAvailabilityIn(BaseModel):
    """What a professional sets in its own profile: whether it offers remote
    appointments (video call) and at which rate. ``remote_price_cents``
    omitted keeps the current rate; ``same_as_fee`` drops it so remote
    appointments pay the regular fee."""

    remote_enabled: bool
    remote_price_cents: int | None = Field(default=None, ge=0)
    same_as_fee: bool = False


class ProfessionalOut(BaseModel):
    id: int
    name: str
    document: str = ""  # CPF
    specialty_id: int
    specialty_name: str
    specialty_price_cents: int
    clinic_id: int
    clinic_name: str
    clinic_fee_cents: int
    price_cents: int
    slot_minutes: int
    # On-call: availability, the professional's rate and the shifts; plus the
    # clinic's and specialty's on-call values (null: same as regular).
    on_call_enabled: bool = False
    on_call_price_cents: int = 0
    on_call_hours: list[WorkingHoursOut] = []
    clinic_on_call_fee_cents: int | None = None
    specialty_on_call_price_cents: int | None = None
    # Ad-hoc urgent visits and their rate (at least urgent_minimum_cents = 3 x fee)
    urgent_enabled: bool = False
    urgent_price_cents: int = 0
    urgent_minimum_cents: int = 0
    # Remote appointments: whether offered, the professional's remote rate
    # (null: same as the fee) and the rate in force (``remote_fee_cents``);
    # plus the clinic's and specialty's remote values (null: same as regular).
    remote_enabled: bool = False
    remote_price_cents: int | None = None
    remote_fee_cents: int = 0
    clinic_remote_fee_cents: int | None = None
    specialty_remote_price_cents: int | None = None
    daily_limit: int = 0  # 0: unlimited
    monthly_limit: int = 0
    clinic_daily_limit: int = 0  # the clinic's capacity also caps the agenda
    clinic_monthly_limit: int = 0
    working_hours: list[WorkingHoursOut]


class DefaultWeekOut(BaseModel):
    slot_minutes: int
    working_hours: list[WorkingHoursOut]


class TimeOffIn(BaseModel):
    date: date
    start: str | None = None
    end: str | None = None
    reason: str = Field(default="", max_length=200)

    @field_validator("start", "end")
    @classmethod
    def _hh_mm(cls, value: str | None) -> str | None:
        if value is None or not value.strip():
            return None
        value = value.strip()
        if TIME_RE.match(value) is None:
            raise ValueError(f"Invalid time: {value}")
        return value

    @model_validator(mode="after")
    def _range(self):
        if (self.start is None) != (self.end is None):
            raise ValueError("Inform both start and end, or neither for the whole day")
        if self.start is not None and self.start >= self.end:
            raise ValueError("start must be before end")
        return self


class TimeOffOut(TimeOffIn):
    id: int
    professional_id: int


class SlotOut(BaseModel):
    time: str
    status: str  # available | booked | blocked | full (free, but the day is at capacity)
    appointment_id: int | None = None
    patient_name: str | None = None
    on_call: bool = False  # an on-call shift: priced at the on-call rates
    urgent: bool = False  # a booked ad-hoc urgent visit (may be outside the working hours)
    remote: bool = False  # a booked remote appointment (video call)


class DaySlotsOut(BaseModel):
    date: date
    weekday: int
    # The day cannot take another booking: the professional's or the clinic's
    # capacity (daily or monthly limit) is already reached. Free slots of such
    # a day come with status "full".
    full: bool = False
    full_reason: str = ""  # professional | clinic
    slots: list[SlotOut]


SEXES = {"", "F", "M", "O"}
BLOOD_TYPES = {"", "A+", "A-", "B+", "B-", "AB+", "AB-", "O+", "O-"}


class PersonIn(BaseModel):
    """Patient record shared by holders and dependents."""

    name: str = Field(min_length=1, max_length=120)
    birth_date: date | None = None
    sex: str = ""
    document: str = Field(default="", max_length=20)
    identity_card: str = Field(default="", max_length=30)
    health_card: str = Field(default="", max_length=20)
    phone: str = Field(default="", max_length=30)
    email: str = Field(default="", max_length=180)
    mother_name: str = Field(default="", max_length=120)
    father_name: str = Field(default="", max_length=120)
    street: str = Field(default="", max_length=160)
    number: str = Field(default="", max_length=20)
    complement: str = Field(default="", max_length=80)
    district: str = Field(default="", max_length=80)
    city: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=2)
    zip_code: str = Field(default="", max_length=10)
    blood_type: str = ""
    allergies: str = ""
    notes: str = ""

    @field_validator("sex")
    @classmethod
    def _sex(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in SEXES:
            raise ValueError("sex must be F, M or O")
        return value

    @field_validator("blood_type")
    @classmethod
    def _blood(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in BLOOD_TYPES:
            raise ValueError("Invalid blood type")
        return value

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("birth_date")
    @classmethod
    def _not_future(cls, value: date | None) -> date | None:
        if value is not None and value > clock.today():
            raise ValueError("birth_date cannot be in the future")
        return value


class CustomerIn(PersonIn):
    pass


class CustomerOut(CustomerIn):
    id: int
    seller_id: int | None = None  # the seller who registered the customer

    model_config = {"from_attributes": True}


class DependentIn(PersonIn):
    birth_date: date  # required for dependents: a patient record needs the age
    kinship: str = Field(default="", max_length=30)


class DependentOut(DependentIn):
    id: int
    customer_id: int

    model_config = {"from_attributes": True}


class PlanDiscountIn(BaseModel):
    specialty_id: int
    percent: int = Field(ge=0, le=100)


class PlanDiscountOut(PlanDiscountIn):
    specialty_name: str


class PlanDiscountsPut(BaseModel):
    discounts: list[PlanDiscountIn]


APPOINTMENT_LIMIT_PERIODS = {"month", "subscription"}


class PlanCategoryIn(BaseModel):
    """A category of plans. ``enterprise``: its plans are only available
    through company agreements. ``grace_days``: the waiting period of all its
    plans (replacing each plan's own); null: each plan's."""

    name: str = Field(min_length=1, max_length=80)
    description: str = ""
    enterprise: bool = False
    grace_days: int | None = Field(default=None, ge=0, le=365)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name is required")
        return value


class PlanCategoryOut(PlanCategoryIn):
    id: int
    plans_count: int = 0


class ModalityIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    plan_value_cents: int = Field(ge=0, default=0)
    dependent_limit: int = Field(ge=0, default=0)
    grace_days: int = Field(ge=0, default=0)
    extra_dependent_cents: int = Field(ge=0, default=0)
    discount_percent: int = Field(ge=0, le=100, default=0)
    # Appointments covered by the discount per period; 0 = unlimited.
    # Over the limit the appointment is accepted at the full price.
    appointment_limit: int = Field(ge=0, default=0)
    appointment_limit_period: str = "month"
    # The plan's category; an enterprise category makes it an enterprise plan
    # (only through company agreements). Null: no category.
    category_id: int | None = None

    @field_validator("appointment_limit_period")
    @classmethod
    def _limit_period(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in APPOINTMENT_LIMIT_PERIODS:
            raise ValueError("appointment_limit_period must be month or subscription")
        return value


class ModalityOut(ModalityIn):
    id: int
    discounts: list[PlanDiscountOut] = []
    category_name: str | None = None
    enterprise: bool = False  # only through company agreements
    # The waiting period in force: the category's when it sets one, else grace_days
    effective_grace_days: int = 0


# --- sales regions and sellers -------------------------------------------------

STATES = {
    "AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA",
    "PB", "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO",
}


class RegionAreaIn(BaseModel):
    """Part of the scope: a whole state (``city`` blank) or one municipality."""

    state: str
    city: str = Field(default="", max_length=80)

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in STATES:
            raise ValueError("state must be a Brazilian state (UF), e.g. SP")
        return value

    @field_validator("city")
    @classmethod
    def _city(cls, value: str) -> str:
        return " ".join(value.split())


class RegionAreaOut(RegionAreaIn):
    label: str  # "SP" or "Campinas/SP"


class RegionPlanIn(BaseModel):
    modality_id: int
    discount_percent: int = Field(ge=0, le=100, default=0)  # special discount on the monthly fee


class RegionPlanOut(RegionPlanIn):
    modality_name: str
    plan_value_cents: int  # list price
    value_cents: int  # monthly value with the special discount (no extra dependents)


class RegionIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    description: str = ""
    commission_bp: int = Field(ge=0, le=10000, default=0, description="Basis points: 1 bp = 0.01 %")
    areas: list[RegionAreaIn] = Field(min_length=1)  # the scope: where its sellers may sell
    plans: list[RegionPlanIn] = []  # the only plans its sellers may sell
    # Team overrides by level above the seller (basis points): [direct leader,
    # the leader's leader, ...]. Ancestors beyond the list earn nothing.
    override_bps: list[int] = Field(default=[], max_length=5)

    @field_validator("override_bps")
    @classmethod
    def _override_bps(cls, value: list[int]) -> list[int]:
        if any(rate < 0 or rate > 10000 for rate in value):
            raise ValueError("override rates must be between 0 and 10000 basis points")
        while value and value[-1] == 0:  # trailing levels paid nothing are just absent
            value = value[:-1]
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("name is required")
        return value

    @model_validator(mode="after")
    def _unique(self):
        places = set()
        for area in self.areas:
            key = (area.state, normalize_place(area.city))
            if key in places:
                label = f"{area.city}/{area.state}" if area.city else area.state
                raise ValueError(f"area repeated: {label}")
            places.add(key)
        plans = set()
        for item in self.plans:
            if item.modality_id in plans:
                raise ValueError(f"plan {item.modality_id} repeated")
            plans.add(item.modality_id)
        return self


class RegionOut(BaseModel):
    id: int
    name: str
    description: str
    commission_bp: int
    commission_label: str  # "12.5%"
    areas: list[RegionAreaOut]
    scope: str  # "Campinas/SP, RJ"
    plans: list[RegionPlanOut]
    override_bps: list[int] = []  # by level above the seller
    override_labels: list[str] = []  # "3%", "1.5%"...
    sellers_count: int = 0
    active_sellers_count: int = 0


class SellerIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    document: str = Field(default="", max_length=20)
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=30)
    region_id: int  # every seller belongs to a region
    active: bool = True
    notes: str = ""
    # Team: the seller this one reports to (a seller of the same region; any depth, no cycles).
    leader_id: int | None = None


class SellerOut(SellerIn):
    id: int
    region_name: str
    commission_bp: int  # the region's, in force today
    subscriptions_count: int = 0  # sales recorded
    leader_name: str | None = None
    team_size: int = 0  # sellers reporting directly to it
    organisation_size: int = 0  # every seller below it, all levels
    depth: int = 0  # levels above it (0: reports to nobody)


class SellerProfileIn(BaseModel):
    """What a seller edits about itself (name, region and status are the staff's)."""

    document: str = Field(default="", max_length=20)
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=30)


class SellerMeOut(SellerOut):
    """The seller's own record with the rules of its region and its team."""

    region: RegionOut
    customers_count: int = 0  # the portfolio
    leader: SellerOut | None = None  # the seller it reports to
    team: list[SellerOut] = []  # the sellers reporting directly to it
    organisation: list[SellerOut] = []  # every seller below it (depth relative to it)
    override_bps: list[int] = []  # what its region pays by level above a seller


# --- corporate partnerships (B2B) ----------------------------------------------------

PAYERS = {"company", "shared", "employee"}  # shared: co-payment (company and employee split each fee)


class PartnerIn(BaseModel):
    """A partner company: plans are negotiated with it for its employees."""

    name: str = Field(min_length=1, max_length=160)
    cnpj: str = Field(default="", max_length=18)
    contact_name: str = Field(default="", max_length=120)
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=30)
    city: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=2)
    notes: str = ""
    active: bool = True

    @field_validator("name", "contact_name", "city")
    @classmethod
    def _trim(cls, value: str) -> str:
        return " ".join(value.split())

    @field_validator("cnpj")
    @classmethod
    def _cnpj(cls, value: str) -> str:
        value = value.strip()
        if value and len(re.sub(r"\D", "", value)) != 14:
            raise ValueError("cnpj must have 14 digits")
        return value

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        value = value.strip().upper()
        if value and value not in STATES:
            raise ValueError("state must be a Brazilian state (UF), e.g. SP")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise ValueError("email is not valid")
        return value


class PartnerOut(PartnerIn):
    id: int
    created_at: datetime
    agreements_count: int = 0
    agreements_in_force: int = 0
    employees: int = 0  # enrolled today across its agreements in force


class AgreementPlanIn(BaseModel):
    """A plan the agreement covers and its negotiated conditions."""

    modality_id: int
    discount_percent: int = Field(ge=0, le=100, default=0)  # off the monthly fee, while the minimum is met
    grace_days: int | None = Field(default=None, ge=0, le=365)  # waiting period; null: the plan's
    # Co-payment: the company's default part of this plan's fees; null: the agreement's.
    company_share_percent: int | None = Field(default=None, ge=0, le=100)


class AgreementPlanOut(AgreementPlanIn):
    modality_name: str
    plan_value_cents: int  # list price
    value_cents: int  # monthly value with the negotiated discount (no extra dependents)
    plan_grace_days: int  # the plan's own waiting period


class AgreementIn(BaseModel):
    """A six-month contract: ``ends_on`` is derived from ``starts_on``. Once
    employees are enrolled only ``name``, ``notes`` and ``active`` may change
    (the terms are frozen)."""

    partner_id: int
    name: str = Field(min_length=1, max_length=120)
    starts_on: date
    # The employees always get the conditions; for every month billed to
    # fewer employees than this, the company pays the discounts back.
    min_employees: int = Field(ge=1, le=100000)
    payer: str = "company"
    # Co-payment (payer shared): the company's default part of each fee,
    # 1-99 (the rest is the employee's). Ignored otherwise (100 / 0).
    company_share_percent: int | None = Field(default=None, ge=0, le=100)
    active: bool = True
    notes: str = ""
    plans: list[AgreementPlanIn] = Field(min_length=1)

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("payer")
    @classmethod
    def _payer(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in PAYERS:
            raise ValueError("payer must be company or employee")
        return value

    @model_validator(mode="after")
    def _check(self):
        plans = set()
        for item in self.plans:
            if item.modality_id in plans:
                raise ValueError(f"plan {item.modality_id} repeated")
            plans.add(item.modality_id)
        check_split(self.payer, self.company_share_percent, self.plans)
        return self


def check_split(payer: str, company_share_percent: int | None, plans) -> None:
    """A co-payment needs the company's default part (1-99); its plans may set
    their own (0-100). Otherwise the split follows the payer."""
    if payer == "shared":
        if company_share_percent is None or not 1 <= company_share_percent <= 99:
            raise ValueError("a co-payment needs company_share_percent between 1 and 99")
        return
    if any(item.company_share_percent is not None for item in plans):
        raise ValueError("a plan's own split (company_share_percent) is only for a co-payment (payer shared)")


class AgreementRenewIn(BaseModel):
    """The next six-month contract: the same terms unless new ones are given."""

    name: str | None = Field(default=None, max_length=120)
    min_employees: int | None = Field(default=None, ge=1, le=100000)
    payer: str | None = None
    company_share_percent: int | None = Field(default=None, ge=0, le=100)
    notes: str | None = None
    plans: list[AgreementPlanIn] | None = Field(default=None, min_length=1)
    # Enroll in the new contract every employee enrolled until the end of this one.
    carry_employees: bool = True

    @field_validator("payer")
    @classmethod
    def _payer(cls, value: str | None) -> str | None:
        if value is None:
            return value
        value = value.strip().lower()
        if value not in PAYERS:
            raise ValueError("payer must be company or employee")
        return value


class AgreementOut(BaseModel):
    id: int
    partner_id: int
    partner_name: str
    partner_active: bool
    name: str
    starts_on: date
    ends_on: date  # six months
    min_employees: int
    payer: str  # company | shared | employee
    company_share_percent: int  # the default company part: 100, 0 or the co-payment's
    active: bool
    notes: str
    status: str  # scheduled | in_force | ended | suspended
    terms_frozen: bool  # employees enrolled: only name, notes and activity may change
    plans: list[AgreementPlanOut]
    # Headcount of the reference month: this month, clamped into the agreement
    reference_period: str  # YYYY-MM
    employees: int  # billed in the reference month
    conditions_met: bool  # the minimum is met in the reference month
    missing_employees: int  # how many more are needed to reach it
    shortfall_cents: int = 0  # projected shortfall of the reference month
    shortfall_open_cents: int = 0  # shortfalls of ended months not paid yet
    enrollments_count: int = 0  # enrollments recorded (not cancelled), any date
    created_at: datetime


class InvoiceOut(BaseModel):
    """The company's invoice of a month: its part of its employees' fees due
    in it (all of each fee when it pays them, its share in a co-payment,
    nothing when the employees pay) and, when fewer employees than the
    minimum were billed, the shortfall — the discounts of the month."""

    agreement_id: int
    period: str  # YYYY-MM (month the fees are due)
    payer: str
    status: str  # paid | open | overdue | nothing_due (for the company)
    first_due_on: date | None = None
    last_due_on: date | None = None
    installments: int
    employees: int  # billed in the month
    min_employees: int
    below_minimum: bool
    list_cents: int
    discount_cents: int
    amount_cents: int  # the fees at the negotiated price (both parts)
    company_share_cents: int  # the company's part of the fees
    employee_share_cents: int  # the employees' parts (they pay them on their own)
    company_fees_paid_cents: int
    company_fees_open_cents: int
    company_paid_on: date | None = None
    employee_paid_cents: int
    employee_open_cents: int
    month_ended: bool
    shortfall_cents: int  # projected while the month runs; the paid amount once paid
    shortfall_due_on: date | None = None
    shortfall_paid_on: date | None = None
    shortfall_final: bool  # the month ended (or it was paid): the amount will not change
    company_cents: int  # what the company owes for the month: its part of the fees + the shortfall
    company_open_cents: int  # ... still to pay


class InvoiceLineOut(BaseModel):
    subscription_id: int
    customer_id: int
    customer_name: str
    modality_name: str
    installment_id: int
    sequence: int
    period_start: date
    period_end: date
    due_on: date
    amount_cents: int
    discount_cents: int
    company_share_cents: int
    employee_share_cents: int
    company_paid_on: date | None = None
    employee_paid_on: date | None = None
    paid_on: date | None = None  # the fee, once both parts are in
    paid_cents: int | None = None
    status: str  # paid | open | overdue (the fee)


class InvoiceDetailOut(InvoiceOut):
    lines: list[InvoiceLineOut]


class InvoicePay(BaseModel):
    paid_on: date

    @field_validator("paid_on")
    @classmethod
    def _not_future(cls, value: date) -> date:
        if value > clock.today():
            raise ValueError("paid_on cannot be in the future")
        return value


# --- the company portal (role partner) ---------------------------------------------------


class PortalEmployeeIn(BaseModel):
    """A company enrolls an employee in one of its agreements. The person is
    found by CPF (an existing customer keeps its record) or registered with
    the data given here — only what the enrollment needs: the company never
    sees health records."""

    agreement_id: int
    modality_id: int
    joined_on: date | None = None  # default: today (or the agreement's start, when later)
    # Co-payment: the company's part of this employee's fees, as agreed with it
    # (default: the plan's or the agreement's). Changed later with PUT …/share.
    company_share_percent: int | None = Field(default=None, ge=0, le=100)
    name: str = Field(min_length=1, max_length=120)
    document: str = Field(max_length=20)  # CPF, required: it identifies the person
    birth_date: date | None = None
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=30)

    @field_validator("document")
    @classmethod
    def _cpf(cls, value: str) -> str:
        value = value.strip()
        if len(re.sub(r"\D", "", value)) != 11:
            raise ValueError("document must be a CPF with 11 digits")
        return value

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise ValueError("email is not valid")
        return value

    @field_validator("birth_date")
    @classmethod
    def _not_future(cls, value: date | None) -> date | None:
        if value is not None and value > clock.today():
            raise ValueError("birth_date cannot be in the future")
        return value


class PortalEmployeeCancel(BaseModel):
    cancelled_on: date | None = None  # default: today; never in the past


class PortalEmployeeOut(BaseModel):
    """An employee's enrollment as its company sees it: who, which plan,
    when, what it costs — never the person's health data."""

    subscription_id: int
    agreement_id: int
    agreement_name: str
    customer_id: int
    name: str
    document: str
    email: str
    phone: str
    modality_id: int
    modality_name: str
    joined_on: date
    expires_on: date
    cancelled_on: date | None = None
    status: str  # the subscription's status
    usable_from: date
    grace_days: int  # the waiting period in force (the agreement's, the category's or the plan's)
    dependents_count: int
    monthly_amount_cents: int
    list_amount_cents: int
    discount_percent: int
    installments_paid: int
    installments_total: int
    birth_date: date | None = None
    # Who pays: the company's part in force today and each side's monthly amount
    payer: str
    company_share_percent: int
    company_monthly_cents: int
    employee_monthly_cents: int
    share_history: list["EnrollmentShareOut"] = []


SEXES_PORTAL = {"", "F", "M", "O"}


def _portal_cpf(value: str, required: bool) -> str:
    value = value.strip()
    if (value or required) and len(re.sub(r"\D", "", value)) != 11:
        raise ValueError("document must be a CPF with 11 digits")
    return value


class PortalEmployeeUpdate(BaseModel):
    """What the company may change about its employee: the person's
    identification and contact (never health data) and the enrollment's plan
    (one the agreement covers) or, before it starts, its start."""

    name: str = Field(min_length=1, max_length=120)
    document: str = Field(max_length=20)  # CPF
    birth_date: date | None = None
    email: str = Field(default="", max_length=180)
    phone: str = Field(default="", max_length=30)
    modality_id: int | None = None  # omitted: unchanged
    joined_on: date | None = None  # omitted: unchanged; only before the enrollment starts

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("document")
    @classmethod
    def _cpf(cls, value: str) -> str:
        return _portal_cpf(value, required=True)

    @field_validator("email")
    @classmethod
    def _email(cls, value: str) -> str:
        value = value.strip()
        if value and not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", value):
            raise ValueError("email is not valid")
        return value

    @field_validator("birth_date")
    @classmethod
    def _not_future(cls, value: date | None) -> date | None:
        if value is not None and value > clock.today():
            raise ValueError("birth_date cannot be in the future")
        return value


class PortalDependentIn(BaseModel):
    """A dependent as the company records it: who and the kinship — never
    health data (the staff and the employee keep that)."""

    name: str = Field(min_length=1, max_length=120)
    birth_date: date  # a patient record needs the age
    kinship: str = Field(default="", max_length=30)
    document: str = Field(default="", max_length=20)  # CPF, optional
    sex: str = ""

    @field_validator("name")
    @classmethod
    def _name(cls, value: str) -> str:
        value = " ".join(value.split())
        if not value:
            raise ValueError("name is required")
        return value

    @field_validator("document")
    @classmethod
    def _cpf(cls, value: str) -> str:
        return _portal_cpf(value, required=False)

    @field_validator("sex")
    @classmethod
    def _sex(cls, value: str) -> str:
        value = value.strip().upper()
        if value not in SEXES_PORTAL:
            raise ValueError("sex must be F, M or O")
        return value

    @field_validator("birth_date")
    @classmethod
    def _not_future(cls, value: date) -> date:
        if value > clock.today():
            raise ValueError("birth_date cannot be in the future")
        return value


class PortalDependentOut(BaseModel):
    id: int
    name: str
    birth_date: date | None = None
    kinship: str = ""
    document: str = ""
    sex: str = ""


class PortalEmployeeDetailOut(PortalEmployeeOut):
    """An employee with its dependents and what they cost: the plan's
    dependents included and the price of each one above them."""

    dependents: list[PortalDependentOut] = []
    dependent_limit: int
    extra_dependent_cents: int


class PortalCancelOut(BaseModel):
    withdrawn: bool  # the enrollment had not started: removed altogether
    employee: PortalEmployeeOut | None = None  # the cancelled enrollment otherwise


class PortalOut(BaseModel):
    """The company's home in the portal."""

    partner: PartnerOut
    agreements: list[AgreementOut]
    employees_enrolled: int  # enrollments in force today, every agreement
    shortfall_open_cents: int  # shortfalls of ended months not paid yet
    company_open_cents: int  # everything the company still owes (due so far)


# --- co-payment: the split and the statements ---------------------------------------------


class ShareChangeIn(BaseModel):
    """A new split of an employee's fees (co-payment), agreed between the
    company and the employee: from ``effective_from`` (default today) on,
    the company pays ``company_share_percent`` and the employee the rest.
    Fees with a part already paid keep their split."""

    company_share_percent: int = Field(ge=0, le=100)
    effective_from: date | None = None
    note: str = Field(default="", max_length=200)


class EnrollmentShareOut(BaseModel):
    company_share_percent: int
    employee_share_percent: int
    effective_from: date
    note: str = ""
    created_by_name: str | None = None
    created_at: datetime


class ShareChangeOut(BaseModel):
    """The enrollments a split change reached."""

    changed: int
    effective_from: date
    company_share_percent: int


class StatementMonth(BaseModel):
    period: str  # YYYY-MM (the month the fees are due) or "total"
    employees: int
    installments: int
    fees_cents: int  # both parts
    company_share_cents: int
    employee_share_cents: int
    shortfall_cents: int
    company_cents: int  # the company's part of the fees + the shortfall
    company_paid_cents: int
    company_open_cents: int
    employee_paid_cents: int
    employee_open_cents: int


class StatementEmployee(BaseModel):
    customer_id: int
    name: str
    document: str
    agreements: list[str]
    plans: list[str]
    company_share_percent: int | None = None  # in force today (latest enrollment)
    months: int  # months billed in the range
    fees_cents: int
    company_share_cents: int
    employee_share_cents: int
    company_paid_cents: int
    company_open_cents: int
    employee_paid_cents: int
    employee_open_cents: int


class CompanyStatementOut(BaseModel):
    """What a company pays and what its employees pay, month by month and
    employee by employee (by the month the fees are due)."""

    partner_id: int
    partner_name: str
    agreement_id: int | None = None
    start: str  # YYYY-MM
    end: str
    totals: StatementMonth  # period "total"
    months: list[StatementMonth]
    employees: list[StatementEmployee]


class EmployeeStatementLine(BaseModel):
    subscription_id: int
    modality_name: str
    partner_name: str | None = None  # the employer's corporate plan; null: an individual plan
    agreement_name: str | None = None
    sequence: int
    period_start: date
    period_end: date
    due_on: date
    amount_cents: int  # the fee (both parts)
    discount_cents: int
    company_cents: int  # paid by the company
    own_cents: int  # the employee's part
    own_paid_on: date | None = None
    own_paid_cents: int | None = None
    own_status: str  # paid | open | overdue | none (nothing to pay)
    company_paid_on: date | None = None
    status: str  # the fee: paid | open | overdue


class EmployeeStatementTotals(BaseModel):
    fees_cents: int
    company_cents: int
    own_cents: int
    own_paid_cents: int
    own_open_cents: int
    own_overdue_cents: int


class EmployeeStatementOut(BaseModel):
    """A customer's own payments — its part of every fee (all of it for an
    individual plan; its share in a corporate co-payment) — and what its
    company paid, by the month the fees are due."""

    customer_id: int
    customer_name: str
    start: str  # YYYY-MM
    end: str
    totals: EmployeeStatementTotals
    lines: list[EmployeeStatementLine]


class SaleQuoteOut(BaseModel):
    """Preview of a plan sale: the price and, for a seller, whether its
    region allows it (``allowed``/``reason``) and the terms it would get."""

    customer_id: int
    modality_id: int
    seller_id: int | None = None
    region_id: int | None = None
    region_name: str | None = None
    allowed: bool = True
    reason: str | None = None
    dependents_count: int
    list_amount_cents: int
    discount_percent: int = 0
    monthly_amount_cents: int
    commission_bp: int = 0
    commission_cents: int = 0  # on one monthly fee paid in full
    # Corporate enrollment: the agreement's conditions (always granted) and
    # its minimum of employees in the join month, counting this enrollment
    agreement_id: int | None = None
    agreement_name: str | None = None
    partner_name: str | None = None
    payer: str | None = None  # company | employee
    agreement_ends_on: date | None = None  # the enrollment ends with the contract
    min_employees: int | None = None
    employees: int | None = None  # billed in the join month, this one included
    conditions_met: bool | None = None  # False: the company pays the month's discounts back
    negotiated_discount_percent: int | None = None
    grace_days: int | None = None  # waiting period that would apply
    company_share_percent: int | None = None  # who pays: the company's part
    company_monthly_cents: int | None = None
    employee_monthly_cents: int | None = None


class SaleOverrideOut(BaseModel):
    level: int
    seller_id: int
    seller_name: str
    rate_bp: int
    earned_cents: int = 0  # on the installments paid so far


class SubscriptionIn(BaseModel):
    """Status is derived from the dates and payments, so it is not an input."""

    customer_id: int
    modality_id: int
    joined_on: date
    expires_on: date
    # Sale by a seller: bound by the seller's region (see app.sales). Staff only.
    seller_id: int | None = None
    # Corporate enrollment (B2B): the agreement of the employee's company.
    # Staff only; never together with a seller. Omitted on edit: unchanged.
    agreement_id: int | None = None
    # Co-payment: the company's part of this employee's fees (default: the
    # plan's or the agreement's). Only on create; then PUT /{id}/share.
    company_share_percent: int | None = Field(default=None, ge=0, le=100)
    # Plan upgrade: replace the customer's current subscription, which ends
    # the day before ``joined_on`` (only on create).
    upgrade: bool = False

    @model_validator(mode="after")
    def _period(self):
        if self.expires_on <= self.joined_on:
            raise ValueError("expires_on must be after joined_on")
        if self.seller_id is not None and self.agreement_id is not None:
            raise ValueError("a corporate enrollment is not a seller's sale: send seller_id or agreement_id, not both")
        return self


SALE_CHANGE_KINDS = {"edit", "cancel"}


class SaleChangeIn(BaseModel):
    """What a seller asks on one of its sales. ``edit``: the new plan and/or
    period (omitted fields keep their value); ``cancel``: the date (default
    today). The customer confirms it before anything changes."""

    kind: str
    modality_id: int | None = None
    joined_on: date | None = None
    expires_on: date | None = None
    cancelled_on: date | None = None
    note: str = Field(default="", max_length=500)

    @field_validator("kind")
    @classmethod
    def _kind(cls, value: str) -> str:
        value = value.strip().lower()
        if value not in SALE_CHANGE_KINDS:
            raise ValueError("kind must be edit or cancel")
        return value

    @model_validator(mode="after")
    def _fields(self):
        if self.kind == "edit" and self.modality_id is None and self.joined_on is None and self.expires_on is None:
            raise ValueError("an edit needs a new plan, join date or expiry date")
        if self.kind == "cancel" and (self.modality_id is not None or self.joined_on is not None or self.expires_on is not None):
            raise ValueError("a cancellation takes only the cancellation date")
        return self


class SaleChangeDecision(BaseModel):
    note: str = Field(default="", max_length=500)


class SaleChangeOut(BaseModel):
    id: int
    subscription_id: int
    seller_id: int
    seller_name: str
    kind: str  # edit | cancel
    status: str  # pending | confirmed | rejected | withdrawn
    modality_id: int | None = None
    modality_name: str | None = None
    joined_on: date | None = None
    expires_on: date | None = None
    cancelled_on: date | None = None
    note: str = ""
    summary: str  # "Plan Foo -> Plan Bar, expires 30/06/2027" / "Cancel on 31/10/2026"
    created_at: datetime
    decided_at: datetime | None = None
    decided_by_name: str | None = None
    decision_note: str = ""


class InstallmentPay(BaseModel):
    """Set ``paid_on`` to register the payment; send null to undo it. A
    corporate fee has two parts: ``part`` (company | employee) registers or
    undoes one of them; omitted, every part still open (or, to undo, both)."""

    paid_on: date | None
    paid_cents: int | None = Field(default=None, ge=0)
    part: str | None = None

    @field_validator("part")
    @classmethod
    def _part(cls, value: str | None) -> str | None:
        if value is not None and value not in {"company", "employee"}:
            raise ValueError("part must be company or employee")
        return value

    @field_validator("paid_on")
    @classmethod
    def _not_future(cls, value: date | None) -> date | None:
        if value is not None and value > clock.today():
            raise ValueError("paid_on cannot be in the future")
        return value


class InstallmentOut(BaseModel):
    id: int
    sequence: int
    period_start: date
    period_end: date
    due_on: date
    amount_cents: int
    discount_cents: int = 0  # region's special discount included in the amount
    paid_on: date | None
    paid_cents: int | None
    tax_cents: int
    status: str  # paid | overdue | open
    appointments_used: int = 0  # covered appointments dated in this period
    commission_cents: int | None = 0  # seller's commission on this payment (paid only); null for clients
    # Corporate enrollments: the company's and the employee's parts and their payments
    company_share_cents: int = 0
    employee_share_cents: int = 0
    company_paid_on: date | None = None
    company_paid_cents: int | None = None
    employee_paid_on: date | None = None
    employee_paid_cents: int | None = None


class SubscriptionCancel(BaseModel):
    cancelled_on: date | None = None  # default: today


class SubscriptionOut(BaseModel):
    id: int
    customer_id: int
    customer_name: str
    modality_id: int
    modality_name: str
    joined_on: date
    expires_on: date
    cancelled_on: date | None
    status: str
    usable_from: date
    dependents_count: int
    monthly_amount_cents: int  # charged: after the region's special discount
    list_amount_cents: int = 0  # before the special discount
    # Sale by a seller: terms snapshotted when sold (see app.sales)
    seller_id: int | None = None
    seller_name: str | None = None
    region_id: int | None = None
    region_name: str | None = None
    # Commission: internal, null for client logins
    commission_bp: int | None = 0
    discount_percent: int = 0  # region's special discount on the monthly fee
    commission_earned_cents: int | None = 0  # on the installments paid so far
    # The seller's ancestors at the time of the sale and their override (snapshot; internal, empty for clients)
    overrides: list["SaleOverrideOut"] = []
    # Corporate enrollment (B2B): the employee's company and its agreement.
    # ``discount_percent`` is then the negotiated discount, snapshotted.
    agreement_id: int | None = None
    agreement_name: str | None = None
    partner_id: int | None = None
    partner_name: str | None = None
    paid_by_company: bool = False
    # The company's headcount this month against its minimum: internal, null for client logins
    agreement_min_employees: int | None = None
    agreement_employees: int | None = None
    agreement_conditions_met: bool | None = None
    # Who pays: the payer mode, the company's part in force today and the
    # monthly amount of each side (the employee pays its part on its own)
    payer: str | None = None  # company | shared | employee
    company_share_percent: int | None = None
    company_monthly_cents: int = 0
    employee_monthly_cents: int = 0
    share_history: list["EnrollmentShareOut"] = []
    # Plan upgrade: the subscription this one replaced / was replaced by
    upgraded_from_id: int | None = None
    upgraded_to_id: int | None = None
    # A seller's edit/cancellation awaiting the customer's confirmation
    pending_change: SaleChangeOut | None = None
    installments_total: int
    installments_paid: int
    installments_overdue: int
    next_due_on: date | None
    # Plan allowance (see app.usage) and its usage in the period of today
    appointment_limit: int = 0  # 0: unlimited
    appointment_limit_period: str = "month"  # month | subscription
    appointments_used: int = 0
    appointments_remaining: int | None = None  # None: unlimited
    usage_window_start: date | None = None  # None: today is outside the subscription
    usage_window_end: date | None = None
    installments: list[InstallmentOut]


class AppointmentIn(BaseModel):
    """Specialty, clinic and amounts are not inputs: they come from the
    professional's record and the price list at scheduling time."""

    customer_id: int
    dependent_id: int | None = None
    professional_id: int
    marked_at: datetime
    status: str = "agendada"
    notes: str = ""
    # An ad-hoc urgent visit: any time outside the professional's on-call
    # shifts (working hours not required), at the urgent rate. Omitted:
    # not urgent on create, unchanged on edit.
    urgent: bool | None = None
    # A remote appointment (video call) in a regular slot of a professional
    # who offers them, at the remote price list. Omitted: in person on
    # create, unchanged on edit. ``meeting_url``: the staff's own link for
    # the call; omitted or empty, the system issues one (see app.remote).
    remote: bool | None = None
    meeting_url: str | None = Field(default=None, max_length=500)

    @field_validator("meeting_url")
    @classmethod
    def _meeting_url(cls, value: str | None) -> str | None:
        if value is None:
            return None
        return clean_meeting_url(value)

    @field_validator("marked_at")
    @classmethod
    def _minute_precision(cls, value: datetime) -> datetime:
        """Slots are HH:mm; seconds, microseconds and offsets are dropped."""
        return normalize_marked_at(value)


class TaxLineOut(BaseModel):
    code: str
    name: str
    level: str
    rate_bp: int
    base_cents: int
    amount_cents: int
    state: str
    city: str
    ibge_code: str


class AppointmentOut(BaseModel):
    id: int
    customer_id: int
    customer_name: str
    dependent_id: int | None
    dependent_name: str | None
    professional_id: int
    professional_name: str
    specialty_id: int
    specialty_name: str
    clinic_id: int
    clinic_name: str
    marked_at: datetime
    status: str
    on_call: bool = False  # on-call service: the amounts below are the on-call price list
    urgent: bool = False  # ad-hoc urgent visit: the professional's urgent rate
    remote: bool = False  # remote appointment (video call): the remote price list
    meeting_url: str = ""  # where to join a remote appointment ("" in person)
    fee_cents: int
    specialty_price_cents: int
    professional_price_cents: int
    gross_cents: int
    modality_id: int | None
    modality_name: str | None
    discount_percent: int
    discount_cents: int
    amount_cents: int
    # Plan limit snapshot: over the limit there is no discount (full price)
    over_limit: bool = False
    plan_limit: int = 0  # 0: unlimited
    plan_limit_period: str = ""
    tax_cents: int
    net_cents: int
    taxes: list[TaxLineOut]
    notes: str
    created_by_id: int | None
    created_by_name: str | None
    created_at: datetime
    # Where to go
    clinic_address: str
    clinic_phone: str
    clinic_directions: str
    clinic_latitude: float | None
    clinic_longitude: float | None
    clinic_maps_url: str
    # Attendance audit
    attendance: str  # awaiting | checked_in | in_progress | checked_out | completed | no_show | cancelled
    checked_in_at: datetime | None
    checked_in_by_name: str | None
    checked_out_at: datetime | None
    checked_out_by_name: str | None
    can_check_in: bool
    can_check_out: bool
    check_in_opens_at: datetime
    check_in_closes_at: datetime
    # Start of the visit: the patient's virtual card code was verified
    started_at: datetime | None
    started_by_name: str | None
    can_start: bool  # for the current user: staff or the appointment's professional
    start_locked_until: datetime | None  # after too many wrong codes
    # Professional's signature: what completes the appointment
    signed_at: datetime | None
    signed_by_name: str | None
    signature_image: str | None = None  # the drawing (JPEG data URL)
    awaiting_signature: bool  # scheduled, slot started, not signed yet
    can_sign: bool  # for the current user
    can_revoke_signature: bool  # for the current user
    prescriptions_count: int = 0  # prescription files attached (see /appointments/{id}/prescriptions)


class PrescriptionOut(BaseModel):
    """A prescription file attached to an appointment, with a signed download
    link valid until ``link_expires_at`` (30 days at most)."""

    id: int
    appointment_id: int
    filename: str
    content_type: str
    size_bytes: int
    sha256: str
    note: str = ""
    uploaded_at: datetime
    uploaded_by_name: str | None
    download_url: str  # API path with the signed token
    link_expires_at: datetime
    retain_until: date  # end of the legal retention (10 years after the visit)
    can_delete: bool  # for the current user
    # Digital signature (ICP-Brasil, CAdES detached): what makes it valid for pharmacies
    signed: bool = False
    signed_at: datetime | None = None
    signer_name: str = ""  # certificate subject
    signer_document: str = ""  # CPF from the certificate
    certificate_issuer: str = ""
    certificate_serial: str = ""
    certificate_not_after: datetime | None = None
    signature_policy: str = ""  # policy OID (ICP-Brasil AD-RB) when referenced
    signature_url: str = ""  # the .p7s, same signed token
    can_sign: bool = False  # for the current user: the appointment's professional, unsigned, not cancelled


class SignatureCheckOut(BaseModel):
    name: str
    ok: bool
    detail: str = ""


class SignatureReportOut(BaseModel):
    """The system's own verification of a CAdES signature (see app.cms)."""

    valid: bool
    checks: list[SignatureCheckOut]
    signer_name: str = ""
    signer_document: str = ""  # CPF
    certificate_issuer: str = ""
    certificate_serial: str = ""
    certificate_not_before: datetime | None = None
    certificate_not_after: datetime | None = None
    chain: list[str] = []  # subject CNs, signer first
    signing_time: datetime | None = None
    digest_sha256: str = ""
    algorithm: str = ""
    policy_oid: str = ""
    standard: str = "CAdES-BES detached (CMS, RFC 5652 / ETSI EN 319 122-1)"
    trust_bundle_configured: bool = True


class PublicVerificationOut(SignatureReportOut):
    """What a pharmacy gets when it checks a file + .p7s pair: the
    verification and whether the signer is a professional of the network."""

    professional_registered: bool = False
    professional_name: str | None = None
    professional_specialty: str | None = None


class PrescriptionsOut(BaseModel):
    appointment_id: int
    items: list[PrescriptionOut]
    max_files: int
    remaining: int
    can_attach: bool  # for the current user (never on a cancelled appointment)
    accepted: str  # human description of the accepted types
    max_bytes: int
    link_days: int
    signing_available: bool = True  # the ICP-Brasil trust bundle is configured on the server


# --- end-to-end chat of remote appointments (see app.chat) ---------------------------


class ChatKeyIn(BaseModel):
    """The user's identity public key (ECDH P-256, JWK) generated in its browser."""

    public_key: dict


class ChatKeyOut(BaseModel):
    public_key: dict | None
    fingerprint: str | None
    updated_at: datetime | None


class ChatParticipantOut(BaseModel):
    user_id: int
    name: str
    side: str  # customer | professional
    fingerprint: str | None  # None: the user has not set up its chat key yet
    public_key: dict | None
    has_key: bool  # an envelope of the conversation key exists for its current identity key


class ConversationKeyIn(BaseModel):
    """The conversation key wrapped for one participant's identity key."""

    recipient_fingerprint: str = Field(min_length=64, max_length=64)
    ephemeral_public_key: dict
    iv: str = Field(min_length=1, max_length=40)
    wrapped_key: str = Field(min_length=1, max_length=400)


class ConversationKeysIn(BaseModel):
    keys: list[ConversationKeyIn] = Field(min_length=1, max_length=20)


class ConversationKeyOut(BaseModel):
    recipient_fingerprint: str
    ephemeral_public_key: dict
    iv: str
    wrapped_key: str
    created_by_id: int | None


class ConversationOut(BaseModel):
    id: int
    appointment_id: int
    open: bool
    closed_reason: str = ""
    open_until: datetime
    participants: list[ChatParticipantOut]
    my_fingerprint: str | None  # None: set up the chat key first
    my_key: ConversationKeyOut | None  # the envelope for my current identity key, when one exists
    has_conversation_key: bool  # some envelope exists: the key was generated
    keys_pending: list[str]  # fingerprints of participants still without an envelope (for whoever holds the key)
    safety_code: str | None  # compared by both parties out of band (None until both sides have keys)
    last_message_id: int
    unread: int
    max_attachment_bytes: int
    max_attachments_per_message: int


class ChatAttachmentOut(BaseModel):
    id: int
    size_bytes: int  # of the encrypted blob


class ChatMessageIn(BaseModel):
    ciphertext: str = Field(min_length=1, max_length=90_000)  # base64 of the AES-GCM payload
    iv: str = Field(min_length=1, max_length=40)
    attachment_ids: list[int] = Field(default_factory=list, max_length=5)


class ChatMessageOut(BaseModel):
    id: int
    sender_id: int
    sender_name: str
    side: str
    mine: bool
    ciphertext: str
    iv: str
    created_at: datetime
    attachments: list[ChatAttachmentOut]


class ChatReadIn(BaseModel):
    last_read_message_id: int = Field(ge=0)


class CheckInBody(BaseModel):
    latitude: float | None = Field(default=None, ge=-90, le=90)
    longitude: float | None = Field(default=None, ge=-180, le=180)
    notes: str = ""


class SignBody(BaseModel):
    """A professional's signature: password + authenticator code + drawing (see app.signing)."""

    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=12)
    signature_image: str = Field(min_length=1, max_length=450_000)  # data:image/jpeg;base64,...
    notes: str = Field(default="", max_length=2000)


class RevokeBody(BaseModel):
    """Revoking a signature also asks for the password and the authenticator code."""

    password: str = Field(min_length=1, max_length=200)
    code: str = Field(min_length=6, max_length=12)


class TotpSetupOut(BaseModel):
    secret: str  # base32, typed into the authenticator app
    otpauth_uri: str
    account: str
    issuer: str


class TotpConfirmIn(BaseModel):
    code: str = Field(min_length=6, max_length=12)


class TotpStatusOut(BaseModel):
    enabled: bool
    enabled_at: datetime | None
    pending: bool  # setup started, not confirmed
    sign_locked_until: datetime | None


class StartBody(BaseModel):
    """The code shown on the patient's virtual card ("123456" or "123 456")."""

    code: str = Field(min_length=6, max_length=12)


class VirtualCardOut(BaseModel):
    """The patient's virtual card with the code in force right now."""

    customer_id: int
    dependent_id: int | None
    card_number: str
    patient_name: str
    holder_name: str
    plan_name: str | None
    plan_status: str | None
    valid_until: date | None  # plan expiry
    # Appointments left in the plan allowance of the current period (None: no plan or unlimited)
    appointment_limit: int = 0
    appointments_remaining: int | None = None
    code: str
    period_seconds: int
    seconds_remaining: int
    server_time: datetime


class QuoteOut(BaseModel):
    """Price preview for a customer + professional, before booking."""

    on_call: bool = False  # the slot is an on-call shift: on-call price list
    urgent: bool = False  # quoted as an ad-hoc urgent visit: urgent rate
    remote: bool = False  # quoted as a remote appointment: remote price list
    fee_cents: int
    specialty_price_cents: int
    professional_price_cents: int
    gross_cents: int
    modality_id: int | None
    modality_name: str | None
    discount_percent: int
    discount_cents: int
    amount_cents: int
    tax_cents: int
    net_cents: int
    taxes: list[TaxLineOut]
    # Plan allowance for the period of the appointment date (see app.usage)
    over_limit: bool = False  # the allowance is used up: full price
    plan_limit: int = 0  # 0: unlimited
    plan_limit_period: str = ""
    plan_used: int = 0  # covered appointments already booked in the period
    plan_remaining: int | None = None  # None: unlimited
    plan_window_start: date | None = None
    plan_window_end: date | None = None


# --- reports ------------------------------------------------------------------


class PeriodAmounts(BaseModel):
    period: str  # "2026-W43" or "2026-10"
    start: date
    end: date
    scheduled: int
    completed: int
    cancelled: int
    gross_cents: int
    discount_cents: int
    amount_cents: int
    # Booked appointments over the plan limit (full price) and what they bill
    over_limit: int = 0
    over_limit_cents: int = 0
    # Booked on-call services and what they bill
    on_call: int = 0
    on_call_cents: int = 0
    # Booked urgent visits and what they bill
    urgent: int = 0
    urgent_cents: int = 0
    # Booked remote appointments and what they bill
    remote: int = 0
    remote_cents: int = 0


class BreakdownRow(BaseModel):
    id: int
    name: str
    appointments: int
    completed: int
    cancelled: int
    gross_cents: int
    discount_cents: int
    amount_cents: int
    over_limit: int = 0
    over_limit_cents: int = 0
    on_call: int = 0
    on_call_cents: int = 0
    urgent: int = 0
    urgent_cents: int = 0
    remote: int = 0
    remote_cents: int = 0


class AppointmentsReport(BaseModel):
    granularity: str
    start: date
    end: date
    totals: PeriodAmounts
    periods: list[PeriodAmounts]
    by_professional: list[BreakdownRow]
    by_clinic: list[BreakdownRow]
    by_specialty: list[BreakdownRow]
    by_plan: list[BreakdownRow]


class AttendantRow(BaseModel):
    user_id: int | None
    name: str
    role: str | None
    appointments_booked: int
    appointments_completed: int
    appointments_cancelled: int
    completion_rate: float
    plans_sold: int
    payments_registered: int
    payments_cents: int


class AttendantPeriod(BaseModel):
    period: str
    start: date
    end: date
    rows: list[AttendantRow]


class AttendantsReport(BaseModel):
    granularity: str
    start: date
    end: date
    totals: list[AttendantRow]
    periods: list[AttendantPeriod]


class PlanSalesRow(BaseModel):
    modality_id: int
    name: str
    category_id: int | None = None
    category_name: str | None = None
    enterprise: bool = False
    sold: int
    cancelled: int
    payments: int
    revenue_cents: int
    # The plan's allowance and the appointments booked with it in the range (by service date)
    appointment_limit: int = 0
    appointment_limit_period: str = "month"
    appointments_covered: int = 0
    appointments_over_limit: int = 0
    over_limit_cents: int = 0


class PlanPeriod(BaseModel):
    period: str
    start: date
    end: date
    sold: int
    cancelled: int
    payments: int
    revenue_cents: int
    appointments_covered: int = 0  # booked with a plan, inside its limit (by service date)
    appointments_over_limit: int = 0  # booked over the limit: full price
    over_limit_cents: int = 0


class AgreementSalesRow(BaseModel):
    """Corporate enrollments of one agreement in the range."""

    agreement_id: int
    agreement_name: str
    partner_id: int
    partner_name: str
    payer: str
    min_employees: int
    employees_month: int  # billed this month (clamped into the agreement)
    conditions_met: bool
    enrolled: int  # enrollments recorded in the range (by creation date)
    cancelled: int
    payments: int
    revenue_cents: int  # fees received in the range
    discount_cents: int  # negotiated discount on the fees paid
    shortfall_cents: int = 0  # shortfalls paid by the company in the range
    shortfall_open_cents: int = 0  # shortfalls of ended months not paid yet (today)
    company_paid_cents: int = 0  # received from the company in the range (its part of the fees)
    employee_paid_cents: int = 0  # received from the employees in the range (their parts)


class PlansReport(BaseModel):
    granularity: str
    start: date
    end: date
    active_subscriptions: int
    overdue_subscriptions: int
    pending_subscriptions: int
    totals: PlanPeriod
    periods: list[PlanPeriod]
    by_plan: list[PlanSalesRow]
    by_category: list["CategorySalesRow"] = []  # the plans grouped by category (null: no category)
    by_agreement: list[AgreementSalesRow] = []  # corporate (B2B) enrollments


class CategorySalesRow(BaseModel):
    category_id: int | None = None
    name: str  # "No category" for the plans without one
    enterprise: bool = False
    plans: int
    sold: int
    cancelled: int
    payments: int
    revenue_cents: int
    appointments_covered: int = 0
    appointments_over_limit: int = 0


# --- seller commissions ------------------------------------------------------------


class CommissionPeriod(BaseModel):
    period: str  # "2026-W43", "2026-10" or "total"
    start: date
    end: date
    sold: int  # subscriptions sold by sellers (by sale date)
    cancelled: int  # of those, cancelled in the period (by cancellation date)
    payments: int  # installments paid (by payment date)
    revenue_cents: int  # plan fees received
    discount_cents: int = 0  # special discounts granted on those fees
    commission_cents: int  # commission on them
    # Team side: the sales of collaborators (sellers with a leader) and the
    # leaders' override on the fees they received
    team_sold: int = 0
    team_payments: int = 0
    team_revenue_cents: int = 0
    override_cents: int = 0
    total_cents: int = 0  # commission + override


class CommissionRow(BaseModel):
    id: int
    name: str
    region_id: int | None = None  # sellers: the current region
    region_name: str | None = None
    active: bool = True
    commission_bp: int = 0  # regions: the current rate
    sellers_count: int = 0  # regions
    sold: int
    cancelled: int
    payments: int
    revenue_cents: int
    discount_cents: int = 0
    commission_cents: int
    # Sellers: their team
    leader_id: int | None = None
    leader_name: str | None = None
    team_size: int = 0  # direct reports
    organisation_size: int = 0  # all levels below
    team_sold: int = 0
    team_payments: int = 0
    team_revenue_cents: int = 0
    override_cents: int = 0  # earned as a leader on the team's sales
    leader_override_cents: int = 0  # what this seller's own sales paid to its leader(s)
    total_cents: int = 0  # commission + override


class Portfolio(BaseModel):
    """The sales of a seller (or of the filter) as they stand today."""

    customers: int = 0
    subscriptions: int = 0
    active: int = 0
    pending: int = 0
    overdue: int = 0
    expired: int = 0
    cancelled: int = 0
    monthly_cents: int = 0  # fees of the active subscriptions, per month
    installments_due_week: int = 0  # open installments due in the next 7 days
    installments_overdue: int = 0


class CommissionsReport(BaseModel):
    granularity: str
    start: date
    end: date
    portfolio: Portfolio = Portfolio()
    totals: CommissionPeriod
    periods: list[CommissionPeriod]
    by_seller: list[CommissionRow]
    by_region: list[CommissionRow]  # by the region of the sale (snapshot)


class CommissionStatementLine(BaseModel):
    installment_id: int
    subscription_id: int
    sequence: int
    kind: str = "commission"  # commission (own sale) | override (a team sale)
    sold_by: str = ""  # the seller of the sale
    level: int = 0  # override: levels between the seller and this one (1: direct leader)
    customer_name: str
    modality_name: str
    region_name: str | None
    paid_on: date
    paid_cents: int
    discount_percent: int
    commission_bp: int
    commission_cents: int


class CommissionStatement(BaseModel):
    seller_id: int
    seller_name: str
    region_name: str
    start: date
    end: date
    payments: int
    revenue_cents: int
    commission_cents: int  # on own sales
    override_cents: int = 0  # on the team's sales
    total_cents: int = 0
    lines: list[CommissionStatementLine]


# --- professional payouts ---------------------------------------------------------


class PayoutPeriod(BaseModel):
    """What is due to professionals in one week/month (or in the whole range)."""

    period: str
    start: date
    end: date
    completed: int  # appointments paid
    scheduled: int  # booked but not completed yet: not paid
    cancelled: int  # never paid
    payout_cents: int  # earned by signed appointments (service date)
    # Money actually handed over in the period (by payment date)
    advances_cents: int = 0
    installments_cents: int = 0
    paid_cents: int = 0
    unconfirmed_receipts: int = 0


class ProfessionalPayoutRow(BaseModel):
    professional_id: int
    name: str
    specialty_id: int
    specialty_name: str
    clinic_id: int
    clinic_name: str
    fee_cents: int  # current fee of the professional (payouts use the snapshot of each appointment)
    on_call_fee_cents: int = 0  # current on-call rate (0 when not available for on-call)
    on_call: int = 0  # completed on-call services in the range
    on_call_cents: int = 0  # what they pay the professional
    urgent_fee_cents: int = 0  # current urgent rate (0 when not offered)
    urgent: int = 0  # completed urgent visits in the range
    urgent_cents: int = 0
    remote_fee_cents: int = 0  # current remote rate (0 when not offered)
    remote: int = 0  # completed remote appointments in the range
    remote_cents: int = 0
    completed: int
    scheduled: int
    cancelled: int
    payout_cents: int
    periods: list[PayoutPeriod]
    # Payments in the range and the position today
    advances_cents: int = 0
    installments_cents: int = 0
    paid_cents: int = 0
    unconfirmed_receipts: int = 0
    unsettled_cents: int = 0
    advances_outstanding_cents: int = 0
    open_installments_cents: int = 0
    balance_cents: int = 0


class PayoutsReport(BaseModel):
    granularity: str
    start: date
    end: date
    professionals_count: int
    totals: PayoutPeriod
    periods: list[PayoutPeriod]
    professionals: list[ProfessionalPayoutRow]


class PayoutStatementLine(BaseModel):
    """One appointment of the professional's statement."""

    appointment_id: int
    marked_at: datetime
    status: str
    patient: str
    customer_id: int
    dependent_id: int | None
    plan: str | None
    over_limit: bool = False  # billed at full price: the plan allowance was used up
    on_call: bool = False  # on-call service: pays the professional's on-call rate
    urgent: bool = False  # urgent visit: pays the professional's urgent rate
    remote: bool = False  # remote appointment: pays the professional's remote rate
    gross_cents: int
    discount_cents: int
    amount_cents: int
    payout_cents: int  # professional fee snapshot when completed, 0 otherwise
    signed_at: datetime | None


class PayoutStatement(BaseModel):
    start: date
    end: date
    professional_id: int
    name: str
    specialty_id: int
    specialty_name: str
    clinic_id: int
    clinic_name: str
    fee_cents: int
    on_call_fee_cents: int = 0
    on_call: int = 0  # completed on-call services
    on_call_cents: int = 0  # what they pay
    urgent_fee_cents: int = 0
    urgent: int = 0  # completed urgent visits
    urgent_cents: int = 0
    remote_fee_cents: int = 0
    remote: int = 0  # completed remote appointments
    remote_cents: int = 0
    completed: int
    scheduled: int
    cancelled: int
    payout_cents: int
    lines: list[PayoutStatementLine]


# --- payouts: advances, settlements, installments, receipts -----------------------------


class PayeeRef(BaseModel):
    payee_type: str  # professional | clinic
    payee_id: int

    @field_validator("payee_type")
    @classmethod
    def _payee(cls, value: str) -> str:
        if value not in ("professional", "clinic"):
            raise ValueError("payee_type must be professional or clinic")
        return value


class PayeeOut(BaseModel):
    payee_type: str
    payee_id: int
    name: str
    detail: str  # specialty · clinic, or city
    has_login: bool  # professionals with a login sign receipts; clinics are acknowledged by the staff


class AdvanceIn(PayeeRef):
    amount_cents: int = Field(gt=0)
    paid_on: date
    method: str = Field(default="transfer", max_length=20)
    reference: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=2000)


class SettlementIn(PayeeRef):
    until: date  # cutoff: signed appointments up to this service date
    installments: int = Field(default=1, ge=1, le=12)
    first_due_on: date
    interval_days: int = Field(default=30, ge=1, le=120)
    notes: str = Field(default="", max_length=2000)


class InstallmentPaymentIn(BaseModel):
    paid_on: date
    method: str = Field(default="transfer", max_length=20)
    reference: str = Field(default="", max_length=120)
    notes: str = Field(default="", max_length=2000)


class AcknowledgeIn(BaseModel):
    """Clinic receipts: who received the money, recorded by the staff."""

    received_by: str = Field(min_length=1, max_length=120)
    received_by_document: str = Field(default="", max_length=30)


class PaymentOut(BaseModel):
    id: int
    kind: str  # advance | installment
    payee: PayeeOut
    settlement_id: int | None
    sequence: int
    installments_count: int | None  # for installments: k of N
    amount_cents: int
    due_on: date | None
    paid_on: date | None
    method: str
    reference: str
    notes: str
    applied_cents: int  # advances: already deducted
    outstanding_cents: int  # advances: still to deduct
    receipt_number: str | None
    registered_by_name: str | None
    registered_at: datetime | None
    signed_at: datetime | None
    signed_by_name: str | None
    signature_image: str | None = None
    acknowledged_at: datetime | None
    acknowledged_by_name: str | None
    received_by: str
    received_by_document: str
    confirmed: bool  # signed (professional) or acknowledged (clinic)
    can_sign: bool  # current user: the professional's own login
    can_acknowledge: bool  # current user: staff, clinic payee
    can_delete: bool  # advances: unapplied and unconfirmed (admin)
    fingerprint: str | None


class SettlementAppointmentOut(BaseModel):
    appointment_id: int
    marked_at: datetime
    patient: str
    professional_name: str
    clinic_name: str
    fee_cents: int  # what it pays this payee


class AdvanceApplicationOut(BaseModel):
    advance_id: int
    receipt_number: str | None
    paid_on: date | None
    amount_cents: int


class SettlementOut(BaseModel):
    id: int
    closing_id: int | None = None  # set when created by the monthly closing
    payee: PayeeOut
    status: str  # open | awaiting_signature | settled | void
    period_start: date
    period_end: date
    appointments_count: int
    earned_cents: int
    advances_cents: int
    net_cents: int
    installments_count: int
    paid_cents: int
    confirmed_cents: int
    notes: str
    created_by_name: str | None
    created_at: datetime
    voided_at: datetime | None
    can_void: bool
    installments: list[PaymentOut]
    advances_applied: list[AdvanceApplicationOut]
    appointments: list[SettlementAppointmentOut] = []


class ClosingIn(BaseModel):
    period: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # YYYY-MM


class ClosingOut(BaseModel):
    id: int
    period: str
    period_start: date
    period_end: date
    status: str  # scheduled | dispatched | done
    payees_total: int
    settlements_created: int
    payees_skipped: int
    automatic: bool
    triggered_by_name: str | None
    created_at: datetime
    dispatched_at: datetime | None
    finished_at: datetime | None


class PositionOut(BaseModel):
    payee: PayeeOut
    unsettled_appointments: int
    unsettled_cents: int
    advances_outstanding_cents: int
    open_installments: int
    open_installments_cents: int
    receipts_to_confirm: int
    receipts_to_confirm_cents: int
    paid_cents: int
    settled_cents: int
    balance_cents: int


class ReceiptOut(BaseModel):
    """The digital receipt: everything printed on the PDF."""

    payment: PaymentOut
    company_name: str
    company_cnpj: str
    company_address: str
    title: str
    description: str
    settlement: SettlementOut | None
    fingerprint: str
    verify_path: str


# --- notifications and queue --------------------------------------------------------


class NotificationOut(BaseModel):
    id: int
    event: str
    title: str
    body: str
    link: str
    created_at: datetime
    read_at: datetime | None


class NotificationSummary(BaseModel):
    unread: int
    latest: list[NotificationOut]


class QueueJobOut(BaseModel):
    id: int
    kind: str
    event: str
    status: str
    attempts: int
    max_attempts: int
    run_at: datetime
    last_error: str
    created_at: datetime
    finished_at: datetime | None
    to: str | None = None
    subject: str | None = None


class QueueStats(BaseModel):
    pending: int
    queued: int = 0  # published to the broker, waiting for a consumer
    processing: int
    done: int
    dead: int
    broker: str = "memory"  # memory | rabbitmq
    broker_depth: int | None = None  # messages waiting in the broker queue


class WorkerRunOut(BaseModel):
    reminders: dict[str, int]
    closing: dict[str, int] = {}
    agreements: dict[str, int] = {}  # corporate agreements whose minimum of employees changed today
    queue: dict[str, int]


# --- company, tax rules -----------------------------------------------------------


class CompanyIn(AddressIn):
    name: str = Field(min_length=1, max_length=160)
    cnpj: str = Field(default="", max_length=18)
    city: str = Field(default="", max_length=80)
    state: str = Field(default="", max_length=2)
    ibge_code: str = Field(default="", max_length=7)
    tax_regime: str = Field(default="", max_length=40)
    municipal_registration: str = Field(default="", max_length=30)
    phone: str = Field(default="", max_length=30)

    @field_validator("state")
    @classmethod
    def _state(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("ibge_code")
    @classmethod
    def _ibge(cls, value: str) -> str:
        value = value.strip()
        if value and (not value.isdigit() or len(value) != 7):
            raise ValueError("ibge_code must have 7 digits")
        return value


class CompanyOut(CompanyIn, AddressOut):
    id: int


class TaxRuleIn(BaseModel):
    code: str = Field(min_length=1, max_length=20)
    name: str = Field(min_length=1, max_length=120)
    level: str
    rate_bp: int = Field(ge=0, le=10000, description="Basis points: 1 bp = 0.01 %")
    applies_to: str = "all"
    base: str = "amount"
    state: str | None = Field(default=None, max_length=2)
    city: str | None = Field(default=None, max_length=80)
    clinic_id: int | None = None
    specialty_id: int | None = None
    modality_id: int | None = None
    effective_from: date
    effective_to: date | None = None
    active: bool = True
    notes: str = ""

    @field_validator("code")
    @classmethod
    def _code(cls, value: str) -> str:
        return value.strip().upper()

    @field_validator("level")
    @classmethod
    def _level(cls, value: str) -> str:
        if value not in {"federal", "state", "municipal"}:
            raise ValueError("level must be federal, state or municipal")
        return value

    @field_validator("applies_to")
    @classmethod
    def _applies(cls, value: str) -> str:
        if value not in {"appointment", "subscription", "all"}:
            raise ValueError("applies_to must be appointment, subscription or all")
        return value

    @field_validator("base")
    @classmethod
    def _base(cls, value: str) -> str:
        if value not in {"amount", "gross"}:
            raise ValueError("base must be amount or gross")
        return value

    @field_validator("state", "city")
    @classmethod
    def _blank_to_none(cls, value: str | None) -> str | None:
        if value is None:
            return None
        value = value.strip()
        return value or None

    @model_validator(mode="after")
    def _dates(self):
        if self.state is not None:
            self.state = self.state.upper()
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to must not be before effective_from")
        return self


class TaxRuleOut(TaxRuleIn):
    id: int
    clinic_name: str | None = None
    specialty_name: str | None = None
    modality_name: str | None = None
    rate_label: str


class TaxPreviewOut(BaseModel):
    amount_cents: int
    tax_cents: int
    net_cents: int
    lines: list[TaxLineOut]


# --- tax and finance reports ----------------------------------------------------


class TaxBucket(BaseModel):
    key: str
    label: str
    services: int
    base_cents: int
    tax_cents: int


class TaxPeriod(BaseModel):
    period: str
    start: date
    end: date
    services: int
    amount_cents: int
    tax_cents: int
    net_cents: int
    by_code: list[TaxBucket]


class TaxesReport(BaseModel):
    granularity: str
    start: date
    end: date
    basis: str
    totals: TaxPeriod
    periods: list[TaxPeriod]
    by_code: list[TaxBucket]
    by_level: list[TaxBucket]
    by_jurisdiction: list[TaxBucket]
    by_source: list[TaxBucket]


class FinancePeriod(BaseModel):
    period: str
    start: date
    end: date
    appointments_completed: int
    appointments_gross_cents: int
    appointments_discount_cents: int
    appointments_amount_cents: int
    appointments_over_limit: int = 0  # completed over the plan limit, billed at full price
    appointments_over_limit_cents: int = 0
    plan_payments: int
    plan_revenue_cents: int
    # Corporate agreements: shortfalls paid by companies below their minimum
    agreement_charges: int = 0
    agreement_charges_cents: int = 0
    revenue_cents: int
    tax_cents: int
    net_cents: int
    # Payouts handed to professionals and clinics (by payment date) and what is left
    payouts_professionals_cents: int = 0
    payouts_clinics_cents: int = 0
    payouts_cents: int = 0
    result_cents: int = 0


class FinanceReport(BaseModel):
    granularity: str
    start: date
    end: date
    company: CompanyOut
    totals: FinancePeriod
    periods: list[FinancePeriod]


# --- dashboard ----------------------------------------------------------------


class DashboardAppointment(BaseModel):
    id: int
    marked_at: datetime
    patient: str
    professional: str
    clinic: str
    clinic_address: str
    clinic_maps_url: str
    remote: bool = False  # video call: join at meeting_url instead of going to the clinic
    meeting_url: str = ""
    attendance: str
    status: str
    amount_cents: int


class DashboardOut(BaseModel):
    role: str
    today: date
    appointments_today: int
    appointments_upcoming_week: int
    appointments_completed_month: int
    appointments_cancelled_month: int
    next_appointments: list[DashboardAppointment]
    subscriptions_active: int
    subscriptions_overdue: int
    subscriptions_pending: int
    installments_due_week: int
    installments_due_week_cents: int
    unread_notifications: int
    checked_in_today: int
    no_shows_month: int
    # Money (admin only; null for other roles)
    revenue_month_cents: int | None = None
    discounts_month_cents: int | None = None
    taxes_month_cents: int | None = None
    net_month_cents: int | None = None
    plan_revenue_month_cents: int | None = None
    queue_pending: int | None = None
    queue_dead: int | None = None
    # Staff: corporate agreements in force and those below their minimum of employees
    agreements_in_force: int | None = None
    agreements_below_minimum: int | None = None
    # Client only
    plan: SubscriptionOut | None = None
    # Signature control (staff and professionals): visits ended, not signed yet
    awaiting_signature: int = 0
    # Professional only
    payout_month_cents: int | None = None
    signed_month: int | None = None
    to_sign: list[DashboardAppointment] = []
    balance_cents: int | None = None  # what the professional has to receive today
    receipts_to_sign: int | None = None
    # Admin only
    payouts_month_cents: int | None = None
    receipts_unconfirmed: int | None = None
    # Seller only: its sales, portfolio and commission
    seller_name: str | None = None
    region_name: str | None = None
    region_commission_bp: int | None = None
    sales_month: int | None = None
    sales_total: int | None = None
    customers_count: int | None = None
    commission_month_cents: int | None = None
    commission_total_cents: int | None = None
    recent_sales: list["DashboardSale"] = []
    # Sellers leading a team
    team_size: int | None = None
    team_sales_month: int | None = None
    override_month_cents: int | None = None
    override_total_cents: int | None = None
    leader_name: str | None = None  # sellers reporting to a coordinator


class DashboardSale(BaseModel):
    id: int
    seller_name: str | None = None  # team sales on a leader's home
    customer_name: str
    modality_name: str
    joined_on: date
    status: str
    monthly_amount_cents: int
    discount_percent: int = 0


# --- attendance report ------------------------------------------------------------


class AttendancePeriod(BaseModel):
    period: str
    start: date
    end: date
    scheduled: int
    checked_in: int
    checked_out: int
    completed_without_checkin: int
    no_shows: int
    awaiting: int
    cancelled: int
    attendance_rate: float
    avg_arrival_minutes: float | None  # check-in minus slot (negative = early)
    avg_visit_minutes: float | None


class AttendanceReport(BaseModel):
    granularity: str
    start: date
    end: date
    totals: AttendancePeriod
    periods: list[AttendancePeriod]
    by_clinic: list[AttendancePeriod]
    by_professional: list[AttendancePeriod]
