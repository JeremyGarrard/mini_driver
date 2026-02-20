import io
import json
import math

import pandas as pd
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session

from app.auth import get_current_user
from app.database import get_db
from app.models import Flight, User

router = APIRouter()
templates = Jinja2Templates(directory="app/templates")

MAX_FILE_SIZE = 10 * 1024 * 1024  # 10 MB


def _parse_flight_csv(csv_text: str) -> dict:
    df = pd.read_csv(io.StringIO(csv_text))
    df.columns = [c.strip() for c in df.columns]

    time_col = "Time (ms)"
    required = {time_col, "x", "y", "z", "Kpa", "F"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"CSV missing columns: {missing}")

    baseline_kpa = df["Kpa"].head(10).mean()
    df["altitude_ft"] = 44330 * (1 - (df["Kpa"] / baseline_kpa) ** 0.1903) * 3.281
    df["mag"] = (df["x"] ** 2 + df["y"] ** 2 + df["z"] ** 2) ** 0.5
    df["net_accel"] = df["mag"] - 1.0

    def to_list(series):
        return [round(v, 4) if not math.isnan(v) else 0.0 for v in series]

    return {
        "time": to_list(df[time_col]),
        "altitude": to_list(df["altitude_ft"]),
        "net_accel": to_list(df["net_accel"]),
        "x": to_list(df["x"]),
        "y": to_list(df["y"]),
        "z": to_list(df["z"]),
        "temp": to_list(df["F"]),
    }


@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    flights = db.query(Flight).filter(Flight.user_id == current_user.id).order_by(Flight.uploaded_at.desc()).all()
    return templates.TemplateResponse(
        "dashboard.html", {"request": request, "user": current_user, "flights": flights}
    )


@router.get("/upload", response_class=HTMLResponse)
async def upload_page(request: Request, current_user: User = Depends(get_current_user)):
    return templates.TemplateResponse("upload.html", {"request": request, "user": current_user, "error": None})


@router.post("/upload")
async def upload_flight(
    request: Request,
    name: str = Form(...),
    description: str = Form(""),
    file: UploadFile = File(...),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if not file.filename.endswith(".csv"):
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "user": current_user, "error": "Only .csv files are accepted."},
            status_code=400,
        )

    contents = await file.read()
    if len(contents) > MAX_FILE_SIZE:
        return templates.TemplateResponse(
            "upload.html",
            {"request": request, "user": current_user, "error": "File exceeds 10 MB limit."},
            status_code=400,
        )

    flight = Flight(
        user_id=current_user.id,
        name=name,
        description=description,
        csv_data=contents.decode("utf-8", errors="replace"),
    )
    db.add(flight)
    db.commit()
    db.refresh(flight)

    return RedirectResponse(url=f"/flight/{flight.id}", status_code=status.HTTP_303_SEE_OTHER)


@router.get("/flight/{flight_id}", response_class=HTMLResponse)
async def view_flight(
    flight_id: int,
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    flight = db.query(Flight).filter(Flight.id == flight_id).first()
    if not flight:
        raise HTTPException(status_code=404, detail="Flight not found")
    if flight.user_id != current_user.id and not current_user.is_admin:
        raise HTTPException(status_code=403, detail="Not authorized")

    try:
        chart_data = _parse_flight_csv(flight.csv_data)
        error = None
    except Exception as e:
        chart_data = None
        error = str(e)

    return templates.TemplateResponse(
        "flight.html",
        {
            "request": request,
            "user": current_user,
            "flight": flight,
            "chart_data": json.dumps(chart_data) if chart_data else None,
            "error": error,
        },
    )
