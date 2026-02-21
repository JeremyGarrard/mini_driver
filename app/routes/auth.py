import os
import re
import secrets
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Cookie, Depends, Form, Request, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import (
    create_access_token,
    get_current_user,
    hash_password,
    verify_password,
)
from app.database import get_db
from app.models import User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")
BASE_URL = os.getenv("BASE_URL", "http://localhost:8000").rstrip("/")

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"
GOOGLE_USERINFO_URL = "https://www.googleapis.com/oauth2/v2/userinfo"


def _is_first_user(db: Session) -> bool:
    return db.query(User).count() == 0


def _should_be_admin(username: str, db: Session) -> bool:
    if _is_first_user(db):
        return True
    if ADMIN_USERNAME and username == ADMIN_USERNAME:
        return True
    return False


def _unique_username(base: str, db: Session) -> str:
    # Strip non-alphanumeric characters and lowercase
    base = re.sub(r"[^a-z0-9]", "", base.lower()) or "user"
    base = base[:40]
    username = base
    counter = 1
    while db.query(User).filter(User.username == username).first():
        username = f"{base}{counter}"
        counter += 1
    return username


# ── Email / password auth ──────────────────────────────────────────────────

@router.get("/register", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("register.html", {"request": request, "error": None})


@router.post("/register")
async def register(
    request: Request,
    username: str = Form(...),
    email: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    if db.query(User).filter(User.username == username).first():
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "Username already taken."}, status_code=400
        )
    if db.query(User).filter(User.email == email).first():
        return templates.TemplateResponse(
            "register.html", {"request": request, "error": "Email already registered."}, status_code=400
        )

    is_admin = _should_be_admin(username, db)
    user = User(
        username=username,
        email=email,
        password_hash=hash_password(password),
        is_admin=is_admin,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse("login.html", {"request": request, "error": None})


@router.post("/login")
async def login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.username == username).first()
    if not user or not user.password_hash or not verify_password(password, user.password_hash):
        return templates.TemplateResponse(
            "login.html", {"request": request, "error": "Invalid username or password."}, status_code=401
        )

    token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", token, httponly=True, samesite="lax")
    return response


@router.get("/logout")
async def logout(_: User = Depends(get_current_user)):
    response = RedirectResponse(url="/", status_code=status.HTTP_303_SEE_OTHER)
    response.delete_cookie("access_token")
    return response


# ── Google OAuth ───────────────────────────────────────────────────────────

@router.get("/auth/google")
async def google_login():
    state = secrets.token_urlsafe(16)
    params = {
        "client_id": GOOGLE_CLIENT_ID,
        "redirect_uri": f"{BASE_URL}/auth/google/callback",
        "response_type": "code",
        "scope": "openid email profile",
        "state": state,
    }
    url = f"{GOOGLE_AUTH_URL}?{urlencode(params)}"
    response = RedirectResponse(url=url)
    response.set_cookie("oauth_state", state, httponly=True, max_age=300, samesite="lax")
    return response


@router.get("/auth/google/callback")
async def google_callback(
    code: str,
    state: str,
    oauth_state: str = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not oauth_state or state != oauth_state:
        return RedirectResponse(url="/login?error=state", status_code=status.HTTP_303_SEE_OTHER)

    async with httpx.AsyncClient() as client:
        # Exchange code for access token
        token_resp = await client.post(GOOGLE_TOKEN_URL, data={
            "code": code,
            "client_id": GOOGLE_CLIENT_ID,
            "client_secret": GOOGLE_CLIENT_SECRET,
            "redirect_uri": f"{BASE_URL}/auth/google/callback",
            "grant_type": "authorization_code",
        })
        access_token = token_resp.json().get("access_token")
        if not access_token:
            return RedirectResponse(url="/login?error=google", status_code=status.HTTP_303_SEE_OTHER)

        # Fetch user info
        info_resp = await client.get(
            GOOGLE_USERINFO_URL,
            headers={"Authorization": f"Bearer {access_token}"},
        )
        info = info_resp.json()

    email = info.get("email")
    if not email:
        return RedirectResponse(url="/login?error=google", status_code=status.HTTP_303_SEE_OTHER)

    # Find existing user or create one
    user = db.query(User).filter(User.email == email).first()
    if not user:
        username = _unique_username(info.get("name", email.split("@")[0]), db)
        user = User(
            username=username,
            email=email,
            password_hash="",  # Google users have no password
            is_admin=False,
        )
        db.add(user)
        db.commit()
        db.refresh(user)

    jwt_token = create_access_token({"sub": str(user.id)})
    response = RedirectResponse(url="/dashboard", status_code=status.HTTP_303_SEE_OTHER)
    response.set_cookie("access_token", jwt_token, httponly=True, samesite="lax")
    response.delete_cookie("oauth_state")
    return response
