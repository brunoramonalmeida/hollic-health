from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from sqlalchemy.orm import joinedload, selectinload

from ..addresses import format_address, maps_url, osm_url
from ..auth import require_network, require_pricing
from ..database import get_db
from .. import clock
from ..categories import category_plan_ids, individual_use, is_enterprise, plan_grace_days
from ..models import Clinic, Modality, PlanCategory, PlanDiscount, Specialty
from ..schemas import (
    ClinicIn,
    ClinicOut,
    ModalityIn,
    ModalityOut,
    PlanCategoryIn,
    PlanCategoryOut,
    PlanDiscountOut,
    PlanDiscountsPut,
    SpecialtyIn,
    SpecialtyOut,
)

# Reading the catalog is open to every role of the network (clients need it
# to book; partner companies only reach their portal);
# writing moves prices, so it is restricted to admins and supervisors.
WRITE = [Depends(require_pricing)]

clinics = APIRouter(prefix="/clinics", tags=["clinics"], dependencies=[Depends(require_network)])
specialties = APIRouter(
    prefix="/specialties", tags=["specialties"], dependencies=[Depends(require_network)]
)
modalities = APIRouter(
    prefix="/modalities", tags=["modalities"], dependencies=[Depends(require_network)]
)
categories = APIRouter(
    prefix="/plan-categories", tags=["modalities"], dependencies=[Depends(require_network)]
)


def _save(db: Session, row):
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def _delete(db: Session, row):
    try:
        db.delete(row)
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Record is in use")


def clinic_out(row: Clinic) -> ClinicOut:
    data = {column.name: getattr(row, column.name) for column in Clinic.__table__.columns}
    return ClinicOut(**data, address=format_address(row), maps_url=maps_url(row), osm_url=osm_url(row))


@clinics.get("", response_model=list[ClinicOut])
def list_clinics(db: Session = Depends(get_db)):
    return [clinic_out(row) for row in db.query(Clinic).order_by(Clinic.name).all()]


@clinics.post("", response_model=ClinicOut, status_code=201, dependencies=WRITE)
def create_clinic(body: ClinicIn, db: Session = Depends(get_db)):
    return clinic_out(_save(db, Clinic(**body.model_dump())))


@clinics.get("/{item_id}", response_model=ClinicOut)
def get_clinic(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Clinic, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    return clinic_out(row)


@clinics.put("/{item_id}", response_model=ClinicOut, dependencies=WRITE)
def update_clinic(item_id: int, body: ClinicIn, db: Session = Depends(get_db)):
    row = db.get(Clinic, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    return clinic_out(_save(db, row))


@clinics.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_clinic(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Clinic, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Clinic not found")
    _delete(db, row)


@specialties.get("", response_model=list[SpecialtyOut])
def list_specialties(db: Session = Depends(get_db)):
    return db.query(Specialty).order_by(Specialty.name).all()


@specialties.post("", response_model=SpecialtyOut, status_code=201, dependencies=WRITE)
def create_specialty(body: SpecialtyIn, db: Session = Depends(get_db)):
    try:
        return _save(db, Specialty(**body.model_dump()))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Specialty name already exists")


@specialties.get("/{item_id}", response_model=SpecialtyOut)
def get_specialty(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Specialty, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Specialty not found")
    return row


@specialties.put("/{item_id}", response_model=SpecialtyOut, dependencies=WRITE)
def update_specialty(item_id: int, body: SpecialtyIn, db: Session = Depends(get_db)):
    row = db.get(Specialty, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Specialty not found")
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    try:
        return _save(db, row)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Specialty name already exists")


@specialties.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_specialty(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Specialty, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Specialty not found")
    _delete(db, row)


def _modality_out(row: Modality) -> ModalityOut:
    return ModalityOut(
        id=row.id,
        name=row.name,
        plan_value_cents=row.plan_value_cents,
        dependent_limit=row.dependent_limit,
        grace_days=row.grace_days,
        extra_dependent_cents=row.extra_dependent_cents,
        discount_percent=row.discount_percent,
        appointment_limit=row.appointment_limit or 0,
        appointment_limit_period=row.appointment_limit_period or "month",
        category_id=row.category_id,
        category_name=row.category.name if row.category is not None else None,
        enterprise=is_enterprise(row),
        effective_grace_days=plan_grace_days(row),
        discounts=[
            PlanDiscountOut(
                specialty_id=item.specialty_id, percent=item.percent, specialty_name=item.specialty.name
            )
            for item in sorted(row.discounts, key=lambda item: item.specialty.name)
        ],
    )


def _modality_query(db: Session):
    return db.query(Modality).options(
        selectinload(Modality.discounts).joinedload(PlanDiscount.specialty)
    )


def _load_modality(db: Session, item_id: int) -> Modality:
    row = _modality_query(db).filter(Modality.id == item_id).first()
    if row is None:
        raise HTTPException(status_code=404, detail="Modality not found")
    return row


@modalities.get("", response_model=list[ModalityOut])
def list_modalities(db: Session = Depends(get_db)):
    return [_modality_out(row) for row in _modality_query(db).order_by(Modality.name).all()]


def _check_category(db: Session, body: ModalityIn, row: Modality | None) -> None:
    """The category exists; a plan in use by individuals (or offered by a
    sales region) cannot become an enterprise plan."""
    if body.category_id is None:
        return
    category = db.get(PlanCategory, body.category_id)
    if category is None:
        raise HTTPException(status_code=404, detail="Plan category not found")
    if row is not None and category.enterprise and not is_enterprise(row):
        use = individual_use(db, [row.id], clock.today())
        if use.any:
            raise HTTPException(status_code=409, detail=use.message(f'Plan "{row.name}"'))


@modalities.post("", response_model=ModalityOut, status_code=201, dependencies=WRITE)
def create_modality(body: ModalityIn, db: Session = Depends(get_db)):
    _check_category(db, body, None)
    try:
        row = _save(db, Modality(**body.model_dump()))
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plan name already exists")
    return _modality_out(_load_modality(db, row.id))


@modalities.get("/{item_id}", response_model=ModalityOut)
def get_modality(item_id: int, db: Session = Depends(get_db)):
    return _modality_out(_load_modality(db, item_id))


@modalities.put("/{item_id}", response_model=ModalityOut, dependencies=WRITE)
def update_modality(item_id: int, body: ModalityIn, db: Session = Depends(get_db)):
    row = _load_modality(db, item_id)
    _check_category(db, body, row)
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Plan name already exists")
    db.expire_all()
    return _modality_out(_load_modality(db, item_id))


@modalities.put("/{item_id}/discounts", response_model=ModalityOut, dependencies=WRITE)
def put_discounts(item_id: int, body: PlanDiscountsPut, db: Session = Depends(get_db)):
    """Replace the per-specialty overrides of the plan's default discount."""
    row = _load_modality(db, item_id)
    seen = set()
    for item in body.discounts:
        if item.specialty_id in seen:
            raise HTTPException(status_code=400, detail="Specialty repeated")
        seen.add(item.specialty_id)
        if db.get(Specialty, item.specialty_id) is None:
            raise HTTPException(status_code=404, detail=f"Specialty {item.specialty_id} not found")
    db.query(PlanDiscount).filter(PlanDiscount.modality_id == item_id).delete()
    for item in body.discounts:
        db.add(PlanDiscount(modality_id=item_id, specialty_id=item.specialty_id, percent=item.percent))
    db.commit()
    db.expire_all()
    return _modality_out(_load_modality(db, item_id))


@modalities.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_modality(item_id: int, db: Session = Depends(get_db)):
    row = db.get(Modality, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Modality not found")
    _delete(db, row)


# --- plan categories ----------------------------------------------------------------


def _category_out(db: Session, row: PlanCategory) -> PlanCategoryOut:
    return PlanCategoryOut(
        id=row.id,
        name=row.name,
        description=row.description or "",
        enterprise=bool(row.enterprise),
        grace_days=row.grace_days,
        plans_count=db.query(Modality).filter(Modality.category_id == row.id).count(),
    )


def _load_category(db: Session, item_id: int) -> PlanCategory:
    row = db.get(PlanCategory, item_id)
    if row is None:
        raise HTTPException(status_code=404, detail="Plan category not found")
    return row


def _unique_category_name(db: Session, name: str, exclude_id: int | None = None) -> None:
    """Names are unique whatever their case ("Enterprise" / "enterprise")."""
    clash = (
        db.query(PlanCategory)
        .filter(func.lower(PlanCategory.name) == name.lower(), PlanCategory.id != (exclude_id or 0))
        .first()
    )
    if clash is not None:
        raise HTTPException(status_code=409, detail="Category name already exists")


def _commit_category(db: Session, row: PlanCategory) -> PlanCategoryOut:
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="Category name already exists")
    db.refresh(row)
    return _category_out(db, row)


@categories.get("", response_model=list[PlanCategoryOut])
def list_categories(db: Session = Depends(get_db)):
    return [_category_out(db, row) for row in db.query(PlanCategory).order_by(PlanCategory.name).all()]


@categories.post("", response_model=PlanCategoryOut, status_code=201, dependencies=WRITE)
def create_category(body: PlanCategoryIn, db: Session = Depends(get_db)):
    _unique_category_name(db, body.name)
    row = PlanCategory(**body.model_dump())
    db.add(row)
    return _commit_category(db, row)


@categories.get("/{item_id}", response_model=PlanCategoryOut)
def get_category(item_id: int, db: Session = Depends(get_db)):
    return _category_out(db, _load_category(db, item_id))


@categories.put("/{item_id}", response_model=PlanCategoryOut, dependencies=WRITE)
def update_category(item_id: int, body: PlanCategoryIn, db: Session = Depends(get_db)):
    """Making a category enterprise turns its plans into enterprise plans:
    refused while individuals still have them in force or a sales region
    offers them. A new waiting period applies to the next bookings."""
    row = _load_category(db, item_id)
    _unique_category_name(db, body.name, exclude_id=item_id)
    if body.enterprise and not row.enterprise:
        use = individual_use(db, category_plan_ids(db, item_id), clock.today())
        if use.any:
            raise HTTPException(status_code=409, detail=use.message(f'The plans of category "{row.name}"'))
    for key, value in body.model_dump().items():
        setattr(row, key, value)
    return _commit_category(db, row)


@categories.delete("/{item_id}", status_code=204, dependencies=WRITE)
def delete_category(item_id: int, db: Session = Depends(get_db)):
    row = _load_category(db, item_id)
    if category_plan_ids(db, item_id):
        raise HTTPException(status_code=409, detail="The category has plans: move them to another category first")
    db.delete(row)
    db.commit()
