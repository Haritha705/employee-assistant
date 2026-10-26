from typing import Optional
from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthenticatedUser, require_manager
from app.database.connection import get_db
from app.database.metrics import MetricsService

router = APIRouter(prefix="/metrics", tags=["Metrics"])


@router.get("/cost-summary")
async def get_cost_summary(
    start_date: Optional[str] = Query(None, description="Start date filter (YYYY-MM-DD or ISO timestamp)"),
    end_date: Optional[str] = Query(None, description="End date filter (YYYY-MM-DD or ISO timestamp)"),
    manager: AuthenticatedUser = Depends(require_manager),
    db: AsyncSession = Depends(get_db)
):
    """
    GET /metrics/cost-summary
    Manager-only endpoint returning operational metrics, token counts, latency, and estimated cost.
    """
    summary = await MetricsService.get_cost_summary(db, start_date=start_date, end_date=end_date)
    return summary
