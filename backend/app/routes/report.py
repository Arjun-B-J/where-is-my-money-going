"""The Spend Analysis PDF."""
from __future__ import annotations

from fastapi import APIRouter, Depends
from fastapi.responses import Response
from sqlalchemy.orm import Session

from app.clock import utc_now
from app.db import get_db
from app.reports import render_spend_analysis

router = APIRouter(prefix="/report", tags=["report"])


@router.get("/spend-analysis")
async def spend_analysis(db: Session = Depends(get_db)) -> Response:
    """Generate the report.

    Takes tens of seconds: several model passes plus chart rendering. There is
    one report rather than the two overlapping ones this project used to ship;
    the other simply restated the web dashboard.
    """
    pdf = await render_spend_analysis(db)
    filename = f"spend-analysis-{utc_now():%Y%m%d}.pdf"
    return Response(
        content=pdf,
        media_type="application/pdf",
        headers={"Content-Disposition": f'inline; filename="{filename}"'},
    )
