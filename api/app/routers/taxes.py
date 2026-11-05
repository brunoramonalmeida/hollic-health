"""Company settings and tax rules. Staff may read; only administrators change
anything here, since every value moves what is owed to the government."""

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload

from .. import clock
from ..addresses import format_address, maps_url, osm_url
from ..auth import require_admin, require_staff
from ..database import get_db
from ..models import AgreementChargeTax, AppointmentTax, Clinic, InstallmentTax, Modality, Specialty, TaxRule
from ..schemas import CompanyIn, CompanyOut, TaxPreviewOut, TaxRuleIn, TaxRuleOut
from ..taxation import active_rules, get_company, line_out
from ..taxes import ServiceContext, compute_taxes, format_rate, total

company_router = APIRouter(prefix="/company", tags=["taxes"], dependencies=[Depends(require_staff)])
router = APIRouter(prefix="/tax-rules", tags=["taxes"], dependencies=[Depends(require_staff)])
ADMIN = [Depends(require_admin)]


def _company_out(row) -> CompanyOut:
    return CompanyOut(
        id=row.id,
        name=row.name,
        cnpj=row.cnpj or "",
        city=row.city or "",
        state=row.state or "",
        ibge_code=row.ibge_code or "",
        tax_regime=row.tax_regime or "",
        municipal_registration=row.municipal_registration or "",
        phone=row.phone or "",
        street=row.street or "",
        number=row.number or "",
        complement=row.complement or "",
        district=row.district or "",
        zip_code=row.zip_code or "",
        latitude=row.latitude,
        longitude=row.longitude,
        address=format_address(row),
        maps_url=maps_url(row),
        osm_url=osm_url(row),
    )


@company_router.get("", response_model=CompanyOut)
def get_company_settings(db: Session = Depends(get_db)):
    row = get_company(db)
    db.commit()
    return _company_out(row)


@company_router.put("", response_model=CompanyOut, dependencies=ADMIN)
def update_company(body: CompanyIn, db: Session = Depends(get_db)):
    row = get_company(db)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit()
    return _company_out(row)


def _out(row: TaxRule) -> TaxRuleOut:
    return TaxRuleOut(
        id=row.id,
        code=row.code,
        name=row.name,
        level=row.level,
        rate_bp=row.rate_bp,
        rate_label=format_rate(row.rate_bp),
        applies_to=row.applies_to,
        base=row.base,
        state=row.state,
        city=row.city,
        clinic_id=row.clinic_id,
        clinic_name=row.clinic.name if row.clinic else None,
        specialty_id=row.specialty_id,
        specialty_name=row.specialty.name if row.specialty else None,
        modality_id=row.modality_id,
        modality_name=row.modality.name if row.modality else None,
        effective_from=row.effective_from,
        effective_to=row.effective_to,
        active=row.active,
        notes=row.notes or "",
    )


def _query(db: Session):
    return db.query(TaxRule).options(
        joinedload(TaxRule.clinic), joinedload(TaxRule.specialty), joinedload(TaxRule.modality)
    )


def _load(db: Session, item_id: int) -> TaxRule:
    row = _query(db).filter(TaxRule.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Tax rule not found")
    return row


def _check_links(db: Session, body: TaxRuleIn):
    if body.clinic_id is not None and db.get(Clinic, body.clinic_id) is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    if body.specialty_id is not None and db.get(Specialty, body.specialty_id) is None:
        raise HTTPException(status_code=404, detail="Specialty not found")
    if body.modality_id is not None and db.get(Modality, body.modality_id) is None:
        raise HTTPException(status_code=404, detail="Modality not found")


@router.get("", response_model=list[TaxRuleOut])
def list_rules(
    code: str | None = Query(default=None),
    include_inactive: bool = Query(default=True),
    db: Session = Depends(get_db),
):
    query = _query(db)
    if code:
        query = query.filter(TaxRule.code == code.strip().upper())
    if not include_inactive:
        query = query.filter(TaxRule.active.is_(True))
    rows = query.order_by(TaxRule.level, TaxRule.code, TaxRule.effective_from.desc(), TaxRule.id).all()
    return [_out(row) for row in rows]


@router.get("/preview", response_model=TaxPreviewOut)
def preview(
    kind: str = Query(default="appointment"),
    amount_cents: int = Query(default=10000, ge=0),
    gross_cents: int | None = Query(default=None, ge=0),
    on: date | None = Query(default=None),
    clinic_id: int | None = Query(default=None),
    specialty_id: int | None = Query(default=None),
    modality_id: int | None = Query(default=None),
    state: str | None = Query(default=None),
    city: str | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """Which taxes apply to a hypothetical service. The clinic (or the company
    for plan fees) provides the jurisdiction unless state/city are given."""
    if kind not in {"appointment", "subscription"}:
        raise HTTPException(status_code=400, detail="kind must be appointment or subscription")
    ibge = ""
    if clinic_id is not None:
        clinic = db.get(Clinic, clinic_id)
        if clinic is None:
            raise HTTPException(status_code=404, detail="Clinic not found")
        state, city, ibge = state or clinic.state, city or clinic.city, clinic.ibge_code
    elif kind == "subscription":
        company = get_company(db)
        state, city, ibge = state or company.state, city or company.city, company.ibge_code
    ctx = ServiceContext(
        kind=kind,
        on=on or clock.today(),
        state=state or "",
        city=city or "",
        ibge_code=ibge or "",
        clinic_id=clinic_id,
        specialty_id=specialty_id,
        modality_id=modality_id,
    )
    lines = compute_taxes(active_rules(db), ctx, amount_cents=amount_cents, gross_cents=gross_cents)
    tax = total(lines)
    return TaxPreviewOut(
        amount_cents=amount_cents,
        tax_cents=tax,
        net_cents=amount_cents - tax,
        lines=[line_out(line) for line in lines],
    )


@router.post("", response_model=TaxRuleOut, status_code=201, dependencies=ADMIN)
def create_rule(body: TaxRuleIn, db: Session = Depends(get_db)):
    _check_links(db, body)
    row = TaxRule(**body.model_dump())
    db.add(row)
    db.commit()
    return _out(_load(db, row.id))


@router.get("/{item_id}", response_model=TaxRuleOut)
def get_rule(item_id: int, db: Session = Depends(get_db)):
    return _out(_load(db, item_id))


@router.put("/{item_id}", response_model=TaxRuleOut, dependencies=ADMIN)
def update_rule(item_id: int, body: TaxRuleIn, db: Session = Depends(get_db)):
    row = _load(db, item_id)
    _check_links(db, body)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    db.commit()
    db.expire_all()
    return _out(_load(db, item_id))


@router.delete("/{item_id}", status_code=204, dependencies=ADMIN)
def delete_rule(item_id: int, db: Session = Depends(get_db)):
    """Rules referenced by stored tax lines are deactivated instead of removed,
    so the accounting history keeps pointing at them."""
    row = _load(db, item_id)
    referenced = (
        db.query(AppointmentTax).filter(AppointmentTax.rule_id == item_id).count()
        + db.query(InstallmentTax).filter(InstallmentTax.rule_id == item_id).count()
        + db.query(AgreementChargeTax).filter(AgreementChargeTax.rule_id == item_id).count()
    )
    if referenced:
        row.active = False
        db.commit()
        return
    db.delete(row)
    db.commit()
