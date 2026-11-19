import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from . import worker
from .database import Base, engine
from .routers import (
    appointments,
    auth,
    catalog,
    chat,
    customers,
    dashboard,
    notifications,
    partners,
    payouts,
    portal,
    prescriptions,
    professionals,
    queue,
    reports,
    sales,
    subscriptions,
    taxes,
    users,
    mobile,
)

Base.metadata.create_all(bind=engine)


@asynccontextmanager
async def lifespan(_app: FastAPI):
    task = asyncio.create_task(worker.loop()) if worker.WORKER_ENABLED else None
    yield
    if task is not None:
        task.cancel()


app = FastAPI(title="Hollic Health", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5174",
        "http://127.0.0.1:5174",
        "http://localhost:3000",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(auth.router)
app.include_router(catalog.clinics)
app.include_router(catalog.specialties)
app.include_router(catalog.modalities)
app.include_router(catalog.categories)
app.include_router(professionals.router)
app.include_router(customers.router)
app.include_router(sales.regions)
app.include_router(sales.me)
app.include_router(sales.sellers)
app.include_router(partners.partners)
app.include_router(partners.agreements)
app.include_router(portal.router)
app.include_router(subscriptions.router)
app.include_router(appointments.router)
app.include_router(prescriptions.router)
app.include_router(prescriptions.public)
app.include_router(chat.keys_router)
app.include_router(chat.router)
app.include_router(users.router)
app.include_router(reports.router)
app.include_router(payouts.router)
app.include_router(notifications.router)
app.include_router(queue.router)
app.include_router(taxes.company_router)
app.include_router(taxes.router)
app.include_router(dashboard.router)
app.include_router(mobile.public)
app.include_router(mobile.router)


@app.get("/")
def root():
    return {"message": "Hollic Health"}
