from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

from app.db import get_session
from app.models import MIP, MRC, SPMIG, HazmatItem, Request as ReqModel
from app.templating import render

router = APIRouter()


@router.get("/", name="dashboard")
def dashboard(request: Request, db: Session = Depends(get_session)):
    recent = db.execute(
        select(ReqModel)
        .options(selectinload(ReqModel.lines))
        .order_by(ReqModel.created_at.desc())
        .limit(10)
    ).scalars().all()

    counts = {
        "mip": db.scalar(select(func.count()).select_from(MIP)) or 0,
        "mrc": db.scalar(select(func.count()).select_from(MRC)) or 0,
        "spmig": db.scalar(select(func.count()).select_from(SPMIG)) or 0,
        "item": db.scalar(select(func.count()).select_from(HazmatItem)) or 0,
    }
    return render(request, "dashboard.html", {"recent": recent, "counts": counts}, nav="home")
