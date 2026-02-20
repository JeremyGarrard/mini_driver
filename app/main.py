import os
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

load_dotenv()

from app.database import Base, engine
from app.models import User  # noqa: F401 — ensure model is registered
from app.models import Flight  # noqa: F401
from app.routes import auth as auth_router
from app.routes import flights as flights_router
from app.routes import admin as admin_router

# Seed admin from env if needed
from app.auth import hash_password
from app.database import SessionLocal

Base.metadata.create_all(bind=engine)

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")

if ADMIN_USERNAME and ADMIN_PASSWORD:
    db = SessionLocal()
    try:
        if not db.query(User).filter(User.username == ADMIN_USERNAME).first():
            admin = User(
                username=ADMIN_USERNAME,
                email=f"{ADMIN_USERNAME}@admin.local",
                password_hash=hash_password(ADMIN_PASSWORD),
                is_admin=True,
            )
            db.add(admin)
            db.commit()
    finally:
        db.close()

app = FastAPI(title="Mini Driver — Rocket Bootcamp")

Path("uploads").mkdir(exist_ok=True)

app.mount("/static", StaticFiles(directory="static"), name="static")

templates = Jinja2Templates(directory="app/templates")

app.include_router(auth_router.router)
app.include_router(flights_router.router)
app.include_router(admin_router.router)


@app.get("/", response_class=HTMLResponse)
async def index(request: Request):
    return templates.TemplateResponse("index.html", {"request": request})
