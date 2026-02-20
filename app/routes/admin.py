from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import require_admin
from app.database import get_db
from app.models import Flight, User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")


@router.get("/admin", response_class=HTMLResponse)
async def admin_view(
    request: Request,
    current_user: User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    users = db.query(User).order_by(User.created_at).all()
    flights = db.query(Flight).order_by(Flight.uploaded_at.desc()).all()
    flights_by_user: dict[int, list[Flight]] = {}
    for f in flights:
        flights_by_user.setdefault(f.user_id, []).append(f)

    return templates.TemplateResponse(
        "admin.html",
        {
            "request": request,
            "user": current_user,
            "users": users,
            "flights_by_user": flights_by_user,
            "total_flights": len(flights),
        },
    )
