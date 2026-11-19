"""Fictional data for the Hollic Health base.

Plans: Plan Foo and Plan Bar (category Individual) and Plan Enterprise
(category Enterprise: only through company agreements, no waiting period).

Run from the api directory:

    .venv/bin/python scripts/seed_demo.py

Safe to run again: it drops and recreates the tables and inserts this load
once more (so schema changes are picked up without migrations).

Logins (password saude123 for all):
    admin       foo.bar@example.com
    supervisor  supervisor@example.com
    attendant   attendant@example.com
    client      foo.holder@example.com  (customer Foo Holder)
    seller      ana.vendas@example.com  (seller Ana Vendas, region São Paulo, leads Bruno)
    seller      bruno.vendas@example.com (seller Bruno Vendas, reports to Ana)

    partner     rh.portal@acme.example.com (company portal of Acme Indústria)
    partner     rh.portal@umbrella.example.com (company portal of Umbrella, co-payment)
    client      daniela@umbrella.example.com (Daniela, co-paying employee of Umbrella)

Corporate partnerships (B2B), six-month contracts: Acme Indústria ("Acme
2026/2", paid by the company, 20% off Plan Foo and no waiting period for a
minimum of 3 employees: 3 enrolled, its invoices due so far paid) and Globex
Serviços ("Globex 2026/2", paid by each employee, minimum 10: 2 enrolled, so
Globex pays the discounts of every month back — September's paid) and
Umbrella Logística ("Umbrella 2026/2", co-payment: the company pays 60 % of
Plan Foo, 50 % of Plan Bar and 70 % of Plan Enterprise by default; Eduardo agreed 80 %, Gabriel 40 %
from next month; Gabriel's part of November is open).
"""

import hashlib
import sys
from datetime import date, datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app import clock
from app.auth import hash_password
from app.database import Base, SessionLocal, engine
from app.models import (
    Agreement,
    AgreementCharge,
    EnrollmentShare,
    AgreementPlan,
    Appointment,
    AppointmentDocument,
    Clinic,
    Company,
    Customer,
    Dependent,
    Modality,
    Partner,
    PlanCategory,
    PlanDiscount,
    Professional,
    PayoutPayment,
    Region,
    RegionArea,
    RegionOverride,
    RegionPlan,
    SaleOverride,
    Seller,
    Specialty,
    Subscription,
    TaxRule,
    TimeOff,
    User,
    WorkingHours,
)
from app.billing import PART_COMPANY, PART_EMPLOYEE, add_months, pay_in_full, register_part, sync_installments
from app.partners import contract_end
from app.pdf import Document
from app.prescriptions import KIND_PRESCRIPTION, STORAGE_DIR, TYPES, store
from app.remote import new_meeting_url
from app.scheduling import DEFAULT_WEEK
from app.taxation import apply_appointment_taxes, apply_charge_taxes, apply_installment_taxes
from app.virtual_card import generate_secret


def main():
    Base.metadata.drop_all(bind=engine)
    # The prescription files of the previous seed go with its tables.
    if STORAGE_DIR.is_dir():
        for stale in STORAGE_DIR.iterdir():
            if stale.is_file():
                stale.unlink()
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        password = hash_password("saude123")
        admin = User(name="Foo Bar", email="foo.bar@example.com", password_hash=password, role="admin")
        supervisor = User(name="Sup Visor", email="supervisor@example.com", password_hash=password, role="supervisor")
        attendant = User(name="Att Endant", email="attendant@example.com", password_hash=password, role="attendant")
        db.add_all([admin, supervisor, attendant])
        clinic = Clinic(
            name="Clinic Foo",
            street="Avenida Paulista",
            number="1578",
            complement="3º andar",
            district="Bela Vista",
            city="Sample",
            state="SP",
            zip_code="01310-200",
            latitude=-23.5614,
            longitude=-46.6559,
            ibge_code="3550308",
            cnpj="00.000.000/0001-00",
            phone="(11) 3000-0000",
            directions="Entrance by the main lobby; take the elevators to the 3rd floor. Paid parking next door.",
            fee_cents=2000,
            on_call_fee_cents=3000,  # charged instead of the fee on on-call services
            remote_fee_cents=0,  # no room is used on a video call: nothing charged
            # No capacity limits: every slot of its professionals can be booked.
        )
        clinic_b = Clinic(
            name="Clinic Bar",
            street="Rua da Assembleia",
            number="10",
            complement="Sala 1201",
            district="Centro",
            city="Example",
            state="RJ",
            zip_code="20011-901",
            latitude=-22.9046,
            longitude=-43.1770,
            ibge_code="3304557",
            cnpj="00.000.000/0002-00",
            phone="(21) 3000-0000",
            directions="Two blocks from the Carioca metro station.",
            fee_cents=3500,
            # Service capacity reserved for the network (the clinic also serves outside it).
            daily_limit=10,
            monthly_limit=200,
        )
        db.add_all([clinic, clinic_b])
        db.add(
            Company(
                id=1,
                name="Hollic Health Ltda",
                cnpj="11.111.111/0001-11",
                city="Sample",
                state="SP",
                ibge_code="3550308",
                tax_regime="Lucro Presumido",
                municipal_registration="1.234.567-8",
                phone="(11) 4000-0000",
                street="Avenida Paulista",
                number="1000",
                complement="Conj. 101",
                district="Bela Vista",
                zip_code="01310-100",
                latitude=-23.5658,
                longitude=-46.6501,
            )
        )
        clinical = Specialty(name="Specialty Foo", price_cents=8000, on_call_price_cents=10000, remote_price_cents=6000)
        skin = Specialty(name="Specialty Bar", price_cents=12000)
        db.add_all([clinical, skin])
        db.flush()

        marina = Professional(
            name="Lorem Ipsum",
            document="123.456.789-09",  # the demo certificate (scripts/make_demo_certificate.py) carries this CPF
            specialty_id=clinical.id,
            clinic_id=clinic.id,
            price_cents=5000,
            slot_minutes=30,
            # Available for on-call (Saturday mornings, see below) at a higher rate.
            on_call_enabled=True,
            on_call_price_cents=8000,
            # Takes ad-hoc urgent visits at three times the fee (never during the shift).
            urgent_enabled=True,
            urgent_price_cents=15000,
            # Offers remote appointments (video call) at a lower rate.
            remote_enabled=True,
            remote_price_cents=4000,
            # Also serves outside the network: at most 6 Hollic Health visits a
            # day, 80 a month.
            daily_limit=6,
            monthly_limit=80,
        )
        paulo = Professional(
            name="Dolor Sit",
            document="987.654.321-00",
            specialty_id=skin.id,
            clinic_id=clinic_b.id,
            price_cents=7000,
            slot_minutes=60,
            daily_limit=4,
            monthly_limit=60,
        )
        db.add_all([marina, paulo])
        helena = Customer(
            name="Foo Holder",
            card_secret=generate_secret(),  # virtual card: the rotating code comes from this
            birth_date=date(1985, 4, 12),
            sex="F",
            document="000.000.000-00",
            health_card="000000000000000",
            phone="(00) 00000-0001",
            email="foo.holder@example.com",
            mother_name="Mater Foo",
            street="Rua Exemplo",
            number="100",
            district="Centro",
            city="Sample",
            state="SP",
            zip_code="00000-000",
        )
        rui = Customer(
            name="Bar Holder",
            birth_date=date(1990, 9, 3),
            sex="M",
            document="000.000.000-01",
            phone="(00) 00000-0002",
            email="bar.holder@example.com",
            city="Example",
            state="SP",
        )
        db.add_all([helena, rui])
        # Plan Foo: 30% off every specialty, 50% off Specialty Foo, and the
        # discount covers 4 appointments per monthly period (over that, full price).
        # Plan categories: the individual plans (each plan's own waiting period)
        # and the enterprise plans — only through company agreements, with the
        # category's waiting period (none) in place of each plan's.
        individual = PlanCategory(name="Individual", description="Plans sold to individuals and families.")
        enterprise = PlanCategory(
            name="Enterprise",
            description="Plans only available through company agreements.",
            enterprise=True,
            grace_days=0,
        )
        db.add_all([individual, enterprise])
        db.flush()
        plan = Modality(
            name="Plan Foo",
            category_id=individual.id,
            plan_value_cents=9900,
            dependent_limit=2,
            grace_days=30,
            extra_dependent_cents=1500,
            discount_percent=30,
            appointment_limit=4,
            appointment_limit_period="month",
        )
        db.add(plan)
        db.flush()
        db.add(PlanDiscount(modality_id=plan.id, specialty_id=clinical.id, percent=50))
        # Tax rules: federal PIS/COFINS on every service, municipal ISS with a
        # default rate, a lower rate in Example/RJ and a specialty-specific rate.
        db.add_all(
            [
                TaxRule(code="PIS", name="PIS", level="federal", rate_bp=65, effective_from=date(2026, 1, 1)),
                TaxRule(code="COFINS", name="COFINS", level="federal", rate_bp=300, effective_from=date(2026, 1, 1)),
                TaxRule(code="ISS", name="ISS", level="municipal", rate_bp=500, effective_from=date(2026, 1, 1), notes="Default rate"),
                TaxRule(
                    code="ISS",
                    name="ISS",
                    level="municipal",
                    rate_bp=200,
                    state="RJ",
                    city="Example",
                    effective_from=date(2026, 1, 1),
                    notes="Example/RJ municipal rate",
                ),
                TaxRule(
                    code="ISS",
                    name="ISS",
                    level="municipal",
                    rate_bp=300,
                    state="SP",
                    city="Sample",
                    specialty_id=skin.id,
                    effective_from=date(2026, 1, 1),
                    notes="Sample/SP: reduced rate for Specialty Bar",
                ),
            ]
        )
        helena_user = User(
            name="Foo Holder",
            email="foo.holder@example.com",
            password_hash=password,
            role="client",
            customer_id=helena.id,
        )
        db.add(helena_user)
        # Professional logins: they sign their appointments and follow their payouts.
        lorem_user = User(
            name="Lorem Ipsum",
            email="lorem.ipsum@example.com",
            password_hash=password,
            role="professional",
            professional_id=marina.id,
            # Demo authenticator already confirmed (RFC 6238 test secret): type it into any TOTP app.
            totp_secret="GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ",
            totp_enabled_at=datetime(2026, 10, 1, 9, 0),
        )
        dolor_user = User(
            name="Dolor Sit",
            email="dolor.sit@example.com",
            password_hash=password,
            role="professional",
            professional_id=paulo.id,
        )
        db.add_all([lorem_user, dolor_user])
        db.flush()

        # Lorem Ipsum works the default week plus an on-call shift on Saturday
        # mornings; Dolor Sit only on Tuesday afternoons and Thursday mornings,
        # and is off on the first Tuesday afternoon.
        db.add_all(
            [
                WorkingHours(professional_id=marina.id, weekday=w, start_time=s, end_time=e)
                for w, s, e in DEFAULT_WEEK
            ]
        )
        db.add(WorkingHours(professional_id=marina.id, weekday=5, start_time="08:00", end_time="12:00", on_call=True))
        db.add_all(
            [
                WorkingHours(professional_id=paulo.id, weekday=1, start_time="14:00", end_time="18:00"),
                WorkingHours(professional_id=paulo.id, weekday=3, start_time="08:00", end_time="12:00"),
                TimeOff(
                    professional_id=paulo.id,
                    date=date(2026, 10, 27),
                    start_time="16:00",
                    end_time="18:00",
                    reason="Congress",
                ),
                TimeOff(professional_id=marina.id, date=date(2026, 10, 30), reason="Day off"),
            ]
        )
        lucas = Dependent(
            customer_id=helena.id,
            name="Ipsum Child",
            card_secret=generate_secret(),
            birth_date=date(2018, 2, 20),
            sex="M",
            kinship="filho(a)",
            mother_name="Foo Holder",
            father_name="Pater Foo",
            street="Rua Exemplo",
            number="100",
            district="Centro",
            city="Sample",
            state="SP",
            zip_code="00000-000",
            allergies="Dipirona",
        )
        db.add(lucas)
        # Sales regions and their sellers. São Paulo covers the whole state,
        # sells Plan Foo with 10% off the monthly fee, pays 10% commission and
        # team overrides of 3% (direct leader) and 1% (the leader's leader);
        # Rio covers only the municipality of Example/RJ, with 5% off and 8%.
        # A second plan is sold only at the counter (no region offers it).
        premium = Modality(
            name="Plan Bar",
            category_id=individual.id,
            plan_value_cents=19900,
            dependent_limit=4,
            grace_days=15,  # (the e2e helpers pick their own plan among those without a waiting period)
            extra_dependent_cents=2500,
            discount_percent=40,
        )
        db.add(premium)
        # An enterprise plan: special discounts (40% off every specialty, 60% off
        # Specialty Foo, unlimited appointments) and the Enterprise category's
        # waiting period (none) in place of its own 30 days.
        corporate = Modality(
            name="Plan Enterprise",
            category_id=enterprise.id,
            plan_value_cents=14900,
            dependent_limit=4,
            grace_days=30,
            extra_dependent_cents=2000,
            discount_percent=40,
        )
        db.add(corporate)
        db.flush()
        db.add(PlanDiscount(modality_id=corporate.id, specialty_id=clinical.id, percent=60))
        region_sp = Region(
            name="São Paulo",
            description="Whole state of São Paulo.",
            commission_bp=1000,
            areas=[RegionArea(state="SP", city="")],
            plans=[RegionPlan(modality_id=plan.id, discount_percent=10)],
            overrides=[RegionOverride(level=1, rate_bp=300), RegionOverride(level=2, rate_bp=100)],
        )
        region_rj = Region(
            name="Rio de Janeiro",
            description="Municipality of Example only.",
            commission_bp=800,
            areas=[RegionArea(state="RJ", city="Example")],
            plans=[RegionPlan(modality_id=plan.id, discount_percent=5)],
        )
        db.add_all([region_sp, region_rj])
        db.flush()
        # Ana coordinates the São Paulo team: Bruno reports to her and she
        # earns the region's level-1 override (3%) on his sales, on top of his commission.
        ana = Seller(name="Ana Vendas", document="111.111.111-11", email="ana.vendas@example.com", phone="(11) 90000-0001", region_id=region_sp.id)
        db.add(ana)
        db.flush()
        bruno = Seller(name="Bruno Vendas", email="bruno.vendas@example.com", phone="(11) 90000-0002", region_id=region_sp.id, leader_id=ana.id)
        db.add_all(
            [
                bruno,
                Seller(name="Carla Vendas", email="carla.vendas@example.com", phone="(21) 90000-0003", region_id=region_rj.id),
                Seller(name="Davi Vendas", region_id=region_rj.id, active=False, notes="Left the team in September."),
            ]
        )
        db.flush()
        # Seller login: Ana records her sales, registers her customers and follows her commission.
        db.add(User(name="Ana Vendas", email="ana.vendas@example.com", password_hash=password, role="seller", seller_id=ana.id))
        db.add(User(name="Bruno Vendas", email="bruno.vendas@example.com", password_hash=password, role="seller", seller_id=bruno.id))
        # Ana registered both holders herself: they are her portfolio.
        helena.seller_id = ana.id
        rui.seller_id = ana.id
        # The terms of the sales (the region's commission and discount, and
        # the leader's override), as POST /subscriptions would snapshot them.
        sold_by_ana = dict(seller_id=ana.id, region_id=region_sp.id, commission_bp=1000, discount_percent=10)
        sold_by_bruno = dict(sold_by_ana, seller_id=bruno.id)
        # Foo Holder: plan paid up to the current month, so appointments are allowed
        # (sold by Ana). Bar Holder: subscription created but never paid (pending)
        # -> blocked (sold by Bruno, of Ana's team).
        today = clock.today()
        paid_plan = Subscription(
            customer_id=helena.id,
            modality_id=plan.id,
            joined_on=date(2026, 1, 10),
            expires_on=date(2027, 1, 9),
            created_by_id=attendant.id,
            **sold_by_ana,
        )
        pending_plan = Subscription(
            customer_id=rui.id,
            modality_id=plan.id,
            joined_on=today,
            expires_on=add_months(today, 12) - timedelta(days=1),
            created_by_id=attendant.id,
            **sold_by_bruno,
        )
        db.add_all([paid_plan, pending_plan])
        db.flush()
        # Bruno's sale pays Ana the level-1 override snapshotted at the sale.
        pending_plan.overrides.append(SaleOverride(level=1, seller_id=ana.id, rate_bp=300))
        sync_installments(db, paid_plan)
        sync_installments(db, pending_plan)
        for item in paid_plan.installments:
            if item.due_on <= today:
                item.paid_on = item.due_on
                item.paid_cents = item.amount_cents
                item.registered_by_id = attendant.id
                apply_installment_taxes(db, paid_plan, item)
        db.flush()
        # 2026-10-26 is a Monday: Lorem Ipsum works 08:00-12:00 in 30-minute slots.
        # Amounts are the price list at scheduling time minus the plan discount (50% on Specialty Foo).
        gross = clinic.fee_cents + clinical.price_cents + marina.price_cents
        discount = gross * 50 // 100
        db.add_all(
            [
                Appointment(
                    customer_id=helena.id,
                    dependent_id=None,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 10, 26, 8, 0),
                    status="agendada",
                    fee_cents=clinic.fee_cents,
                    specialty_price_cents=clinical.price_cents,
                    professional_price_cents=marina.price_cents,
                    gross_cents=gross,
                    modality_id=plan.id,
                    discount_percent=50,
                    discount_cents=discount,
                    amount_cents=gross - discount,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    notes="",
                ),
                Appointment(
                    customer_id=helena.id,
                    dependent_id=lucas.id,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 10, 26, 8, 30),
                    status="agendada",
                    fee_cents=clinic.fee_cents,
                    specialty_price_cents=clinical.price_cents,
                    professional_price_cents=marina.price_cents,
                    gross_cents=gross,
                    modality_id=plan.id,
                    # Fifth appointment of the 10/10-11/09 period: the plan's
                    # allowance of 4 was already used, so it pays the full price.
                    discount_percent=0,
                    discount_cents=0,
                    amount_cents=gross,
                    over_limit=True,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    notes="Next slot of the same professional. Over the plan limit: full price.",
                ),
                # The week before: two visits signed by their professionals (completed),
                # so the payouts report has something due (Lorem Ipsum 5000, Dolor Sit 7000),
                # and one checked-out visit still awaiting Lorem Ipsum's signature.
                Appointment(
                    customer_id=helena.id,
                    dependent_id=None,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 10, 19, 9, 0),
                    status="realizada",
                    fee_cents=clinic.fee_cents,
                    specialty_price_cents=clinical.price_cents,
                    professional_price_cents=marina.price_cents,
                    gross_cents=gross,
                    modality_id=plan.id,
                    discount_percent=50,
                    discount_cents=discount,
                    amount_cents=gross - discount,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    checked_in_at=datetime(2026, 10, 19, 8, 50),
                    checked_in_by_id=attendant.id,
                    checked_out_at=datetime(2026, 10, 19, 9, 25),
                    checked_out_by_id=attendant.id,
                    started_at=datetime(2026, 10, 19, 9, 2),
                    started_by_id=lorem_user.id,
                    signed_at=datetime(2026, 10, 19, 9, 40),
                    signed_by_id=lorem_user.id,
                    notes="Completed visit.",
                ),
                Appointment(
                    customer_id=helena.id,
                    dependent_id=None,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 10, 21, 10, 0),
                    status="agendada",
                    fee_cents=clinic.fee_cents,
                    specialty_price_cents=clinical.price_cents,
                    professional_price_cents=marina.price_cents,
                    gross_cents=gross,
                    modality_id=plan.id,
                    discount_percent=50,
                    discount_cents=discount,
                    amount_cents=gross - discount,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    checked_in_at=datetime(2026, 10, 21, 9, 55),
                    checked_in_by_id=attendant.id,
                    started_at=datetime(2026, 10, 21, 10, 1),
                    started_by_id=attendant.id,
                    checked_out_at=datetime(2026, 10, 21, 10, 30),
                    checked_out_by_id=attendant.id,
                    notes="Visit ended; awaiting the professional's signature.",
                ),
                Appointment(
                    customer_id=helena.id,
                    dependent_id=lucas.id,
                    professional_id=paulo.id,
                    specialty_id=skin.id,
                    clinic_id=clinic_b.id,
                    marked_at=datetime(2026, 10, 20, 14, 0),
                    status="realizada",
                    fee_cents=clinic_b.fee_cents,
                    specialty_price_cents=skin.price_cents,
                    professional_price_cents=paulo.price_cents,
                    gross_cents=clinic_b.fee_cents + skin.price_cents + paulo.price_cents,
                    modality_id=plan.id,
                    discount_percent=plan.discount_percent,
                    discount_cents=(clinic_b.fee_cents + skin.price_cents + paulo.price_cents) * plan.discount_percent // 100,
                    amount_cents=(clinic_b.fee_cents + skin.price_cents + paulo.price_cents)
                    - (clinic_b.fee_cents + skin.price_cents + paulo.price_cents) * plan.discount_percent // 100,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    checked_in_at=datetime(2026, 10, 20, 13, 55),
                    checked_in_by_id=attendant.id,
                    checked_out_at=datetime(2026, 10, 20, 14, 50),
                    checked_out_by_id=attendant.id,
                    started_at=datetime(2026, 10, 20, 14, 1),
                    started_by_id=dolor_user.id,
                    signed_at=datetime(2026, 10, 20, 15, 0),
                    signed_by_id=dolor_user.id,
                    notes="Completed visit.",
                ),
                # An on-call service on a Saturday shift of Lorem Ipsum (next
                # plan period, so it is covered): the on-call price list applies.
                Appointment(
                    customer_id=helena.id,
                    dependent_id=None,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 11, 14, 9, 0),
                    status="agendada",
                    on_call=True,
                    fee_cents=clinic.on_call_fee_cents,
                    specialty_price_cents=clinical.on_call_price_cents,
                    professional_price_cents=marina.on_call_price_cents,
                    gross_cents=clinic.on_call_fee_cents + clinical.on_call_price_cents + marina.on_call_price_cents,
                    modality_id=plan.id,
                    discount_percent=50,
                    discount_cents=(clinic.on_call_fee_cents + clinical.on_call_price_cents + marina.on_call_price_cents) * 50 // 100,
                    amount_cents=(clinic.on_call_fee_cents + clinical.on_call_price_cents + marina.on_call_price_cents)
                    - (clinic.on_call_fee_cents + clinical.on_call_price_cents + marina.on_call_price_cents) * 50 // 100,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    notes="On-call service (Saturday shift).",
                ),
                # An ad-hoc urgent visit on a Wednesday night, outside the
                # working hours: the urgent rate with the regular clinic fee
                # and specialty price.
                Appointment(
                    customer_id=helena.id,
                    dependent_id=None,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 11, 18, 21, 15),
                    status="agendada",
                    urgent=True,
                    fee_cents=clinic.fee_cents,
                    specialty_price_cents=clinical.price_cents,
                    professional_price_cents=marina.urgent_price_cents,
                    gross_cents=clinic.fee_cents + clinical.price_cents + marina.urgent_price_cents,
                    modality_id=plan.id,
                    discount_percent=50,
                    discount_cents=(clinic.fee_cents + clinical.price_cents + marina.urgent_price_cents) * 50 // 100,
                    amount_cents=(clinic.fee_cents + clinical.price_cents + marina.urgent_price_cents)
                    - (clinic.fee_cents + clinical.price_cents + marina.urgent_price_cents) * 50 // 100,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=attendant.id,
                    notes="Ad-hoc urgent visit.",
                ),
                # A remote appointment (video call) in a regular slot of Lorem
                # Ipsum, booked by the patient: the remote price list (clinic 0,
                # specialty 6000, professional 4000) and a meeting link.
                Appointment(
                    customer_id=helena.id,
                    dependent_id=None,
                    professional_id=marina.id,
                    specialty_id=clinical.id,
                    clinic_id=clinic.id,
                    marked_at=datetime(2026, 10, 27, 9, 0),
                    status="agendada",
                    remote=True,
                    meeting_url=new_meeting_url(),
                    fee_cents=clinic.remote_fee_cents,
                    specialty_price_cents=clinical.remote_price_cents,
                    professional_price_cents=marina.remote_price_cents,
                    gross_cents=clinic.remote_fee_cents + clinical.remote_price_cents + marina.remote_price_cents,
                    modality_id=plan.id,
                    discount_percent=50,
                    discount_cents=(clinic.remote_fee_cents + clinical.remote_price_cents + marina.remote_price_cents) * 50 // 100,
                    amount_cents=(clinic.remote_fee_cents + clinical.remote_price_cents + marina.remote_price_cents)
                    - (clinic.remote_fee_cents + clinical.remote_price_cents + marina.remote_price_cents) * 50 // 100,
                    plan_limit=plan.appointment_limit,
                    plan_limit_period=plan.appointment_limit_period,
                    created_by_id=helena_user.id,
                    notes="Remote appointment (video call).",
                ),
            ]
        )
        db.flush()
        for row in db.query(Appointment).all():
            apply_appointment_taxes(db, row)
        # A prescription (PDF) attached by Lorem Ipsum to the visit it signed
        # on 19/10: the patient downloads it from the appointment page.
        signed_visit = db.query(Appointment).filter(Appointment.professional_id == marina.id, Appointment.status == "realizada").first()
        prescription = Document()
        prescription.text("Hollic Health — Medical prescription", size=14, bold=True)
        prescription.text(f"Patient: {helena.name}   Visit: 19/10/2026 09:00   Professional: {marina.name}")
        prescription.gap()
        prescription.text("Dipirona 500 mg — 1 tablet every 6 hours for 3 days.")
        prescription.text("Rest and fluids. Return in 7 days if the symptoms persist.")
        content = prescription.render()
        db.add(
            AppointmentDocument(
                appointment_id=signed_visit.id,
                kind=KIND_PRESCRIPTION,
                filename="prescription-2026-10-19.pdf",
                content_type=TYPES["pdf"][0],
                size_bytes=len(content),
                sha256=hashlib.sha256(content).hexdigest(),
                storage_key=store(content, "pdf"),
                note="Post-visit prescription",
                uploaded_by_id=lorem_user.id,
                uploaded_at=datetime(2026, 10, 19, 9, 45),
            )
        )
        # An advance handed to Lorem Ipsum, with its receipt still to be signed.
        db.add(
            PayoutPayment(
                kind="advance",
                payee_type="professional",
                professional_id=marina.id,
                amount_cents=2000,
                paid_on=date(2026, 10, 20),
                method="pix",
                reference="PIX 8f2a",
                notes="Advance on the October payout.",
                receipt_number="REC-2026-000001",
                registered_by_id=admin.id,
                registered_at=datetime(2026, 10, 20, 11, 0),
            )
        )
        # Corporate partnerships (B2B): plans negotiated directly with companies
        # for their employees, in six-month contracts with frozen terms; below
        # the minimum of employees the company pays the month's discounts back.
        acme = Partner(
            name="Acme Indústria Ltda",
            cnpj="12.345.678/0001-90",
            contact_name="Rita Recursos",
            email="rh@acme.example.com",
            phone="(11) 3500-0000",
            city="Sample",
            state="SP",
            notes="Benefits renegotiated every six months.",
        )
        globex = Partner(
            name="Globex Serviços S.A.",
            cnpj="98.765.432/0001-10",
            contact_name="Gil Benefícios",
            email="beneficios@globex.example.com",
            city="Example",
            state="RJ",
        )
        db.add_all([acme, globex])
        db.flush()
        acme_terms = Agreement(
            partner_id=acme.id,
            name="Acme 2026/2",
            starts_on=date(2026, 10, 1),
            ends_on=contract_end(date(2026, 10, 1)),
            min_employees=3,
            payer="company",  # one invoice a month for every employee
            company_share_percent=100,
            notes="Company pays the plans; employees may upgrade to Plan Bar.",
            created_by_id=admin.id,
            conditions_met=True,
            plans=[
                AgreementPlan(modality_id=plan.id, discount_percent=20, grace_days=0),
                AgreementPlan(modality_id=premium.id, discount_percent=15, grace_days=0),
                AgreementPlan(modality_id=corporate.id, discount_percent=10),
            ],
        )
        globex_terms = Agreement(
            partner_id=globex.id,
            name="Globex 2026/2",
            starts_on=date(2026, 9, 1),
            ends_on=contract_end(date(2026, 9, 1)),
            min_employees=10,
            payer="employee",  # each employee pays at the negotiated price
            company_share_percent=0,
            created_by_id=admin.id,
            plans=[AgreementPlan(modality_id=plan.id, discount_percent=15)],
        )
        db.add_all([acme_terms, globex_terms])
        db.flush()
        # The company portal: Acme's HR enrolls its employees and follows its invoices.
        db.add(User(name="Rita Recursos", email="rh.portal@acme.example.com", password_hash=password, role="partner", partner_id=acme.id))
        employees = [
            ("Alice Acme", "321.654.987-01", acme_terms, "Sample", "SP", plan),
            ("Bento Acme", "321.654.987-02", acme_terms, "Sample", "SP", plan),
            ("Carmen Acme", "321.654.987-03", acme_terms, "Sample", "SP", corporate),  # the enterprise plan
            ("Gustavo Globex", "321.654.987-04", globex_terms, "Example", "RJ", plan),
            ("Heloísa Globex", "321.654.987-05", globex_terms, "Example", "RJ", plan),
        ]
        for name, cpf, terms, city, state, chosen in employees:
            person = Customer(
                name=name,
                document=cpf,
                card_secret=generate_secret(),
                email=f"{name.split()[0].lower()}@{terms.partner.name.split()[0].lower()}.example.com",
                city=city,
                state=state,
            )
            db.add(person)
            db.flush()
            offer = next(item for item in terms.plans if item.modality_id == chosen.id)
            enrollment = Subscription(
                customer_id=person.id,
                modality_id=chosen.id,
                joined_on=terms.starts_on,
                expires_on=terms.ends_on,
                agreement_id=terms.id,
                discount_percent=offer.discount_percent,  # the negotiated discount, snapshotted
                created_by_id=attendant.id,
                # Who pays: the company (Acme) or each employee (Globex).
                shares=[EnrollmentShare(company_share_percent=terms.company_share_percent, effective_from=terms.starts_on, note="enrollment")],
            )
            db.add(enrollment)
            db.flush()
            sync_installments(db, enrollment)
        db.flush()
        # Acme paid its invoices due so far (one per month, for every employee);
        # the Globex employees paid their own fees.
        for row in db.query(Subscription).filter(Subscription.agreement_id.is_not(None)).all():
            for item in row.installments:
                if item.due_on <= today:
                    pay_in_full(row, item, min(item.due_on + timedelta(days=4), today), attendant.id)
                    apply_installment_taxes(db, row, item)
        # Globex bills 2 employees for a minimum of 10: it paid September's
        # shortfall (the discounts of the month); October's is due on 10/11.
        globex_rows = db.query(Subscription).filter(Subscription.agreement_id == globex_terms.id).all()
        september = [item for row in globex_rows for item in row.installments if item.due_on.month == 9]
        if september and today > date(2026, 10, 5):
            charge = AgreementCharge(
                agreement_id=globex_terms.id,
                period="2026-09",
                employees=2,
                min_employees=10,
                amount_cents=sum(item.discount_cents for item in september),
                paid_on=date(2026, 10, 5),
                registered_by_id=attendant.id,
            )
            db.add(charge)
            apply_charge_taxes(db, charge)
        # Co-payment: Umbrella Logística splits each fee with its employees — 60 %
        # by the company on Plan Foo, 50 % on Plan Bar — and agrees other splits
        # with some of them: Eduardo's company part is 80 %, Gabriel's drops to
        # 40 % from next month. The company pays its part of each month on its
        # invoice; each employee pays its own part (Gabriel's November part is open).
        umbrella = Partner(
            name="Umbrella Logística Ltda",
            cnpj="55.444.333/0001-22",
            contact_name="Úrsula Pessoas",
            email="pessoas@umbrella.example.com",
            city="Sample",
            state="SP",
        )
        db.add(umbrella)
        db.flush()
        umbrella_terms = Agreement(
            partner_id=umbrella.id,
            name="Umbrella 2026/2",
            starts_on=date(2026, 10, 1),
            ends_on=contract_end(date(2026, 10, 1)),
            min_employees=3,
            payer="shared",
            company_share_percent=60,
            created_by_id=admin.id,
            conditions_met=True,
            plans=[
                AgreementPlan(modality_id=plan.id, discount_percent=20, grace_days=0),
                AgreementPlan(modality_id=premium.id, discount_percent=15, grace_days=0, company_share_percent=50),
                AgreementPlan(modality_id=corporate.id, discount_percent=10, company_share_percent=70),
            ],
        )
        db.add(umbrella_terms)
        db.flush()
        db.add(User(name="Úrsula Pessoas", email="rh.portal@umbrella.example.com", password_hash=password, role="partner", partner_id=umbrella.id))
        next_month = add_months(today.replace(day=1), 1)
        co_paid = [
            # name, CPF, plan, discount, the agreed splits (from, company %)
            ("Daniela Umbrella", "321.654.987-06", plan, 20, [(umbrella_terms.starts_on, 60)]),
            ("Eduardo Umbrella", "321.654.987-07", plan, 20, [(umbrella_terms.starts_on, 80)]),
            ("Fernanda Umbrella", "321.654.987-08", premium, 15, [(umbrella_terms.starts_on, 50)]),
            ("Gabriel Umbrella", "321.654.987-09", plan, 20, [(umbrella_terms.starts_on, 60), (next_month, 40)]),
        ]
        umbrella_rows = {}
        for name, cpf, chosen, discount, splits in co_paid:
            person = Customer(
                name=name,
                document=cpf,
                card_secret=generate_secret(),
                email=f"{name.split()[0].lower()}@umbrella.example.com",
                city="Sample",
                state="SP",
            )
            db.add(person)
            db.flush()
            enrollment = Subscription(
                customer_id=person.id,
                modality_id=chosen.id,
                joined_on=umbrella_terms.starts_on,
                expires_on=umbrella_terms.ends_on,
                agreement_id=umbrella_terms.id,
                discount_percent=discount,
                created_by_id=attendant.id,
                shares=[
                    EnrollmentShare(company_share_percent=percent, effective_from=day, note="enrollment" if index == 0 else "agreed with the employee")
                    for index, (day, percent) in enumerate(splits)
                ],
            )
            db.add(enrollment)
            db.flush()
            sync_installments(db, enrollment)
            umbrella_rows[name] = (person, enrollment)
        for name, (person, row) in umbrella_rows.items():
            for item in row.installments:
                if item.due_on > today:
                    continue
                paid_on = min(item.due_on + timedelta(days=3), today)
                register_part(item, PART_COMPANY, paid_on, None, attendant.id)  # on the company's invoice
                if not (name.startswith("Gabriel") and item.sequence > 1):
                    register_part(item, PART_EMPLOYEE, paid_on, None, attendant.id)
                if item.paid_on is not None:
                    apply_installment_taxes(db, row, item)
        # Daniela follows her part of the fees in the client area ("My payments").
        db.add(
            User(
                name="Daniela Umbrella",
                email="daniela@umbrella.example.com",
                password_hash=password,
                role="client",
                customer_id=umbrella_rows["Daniela Umbrella"][0].id,
            )
        )
        db.commit()
        print(
            "Seed ready. Logins: foo.bar@example.com (admin), supervisor@, attendant@, "
            "foo.holder@example.com (client), lorem.ipsum@example.com (professional), ana.vendas@example.com (seller, leads Bruno), "
            "rh.portal@acme.example.com and rh.portal@umbrella.example.com (company portals), "
            "daniela@umbrella.example.com (co-paying employee) / saude123"
        )
    finally:
        db.close()


if __name__ == "__main__":
    main()
