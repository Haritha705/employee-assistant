import logging
from typing import Optional, Dict, Any
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from app.config import settings

logger = logging.getLogger(__name__)


class MetricsService:
    @staticmethod
    async def record_metric(
        db: AsyncSession,
        request_id: str,
        thread_id: Optional[str],
        user_id: str,
        model_name: str,
        input_tokens: Optional[int],
        output_tokens: Optional[int],
        total_tokens: Optional[int],
        latency_ms: float,
        status: str,  # 'success' or 'failed'
        retry_count: int = 0,
        error_category: Optional[str] = None
    ):
        """
        Records per-request execution metrics in PostgreSQL/SQLite.
        Safe execution: failures here will not crash the chat flow.
        """
        try:
            stmt = text("""
                INSERT INTO request_metrics (
                    request_id, thread_id, user_id, model_name,
                    input_tokens, output_tokens, total_tokens,
                    latency_ms, status, retry_count, error_category
                ) VALUES (
                    :request_id, :thread_id, :user_id, :model_name,
                    :input_tokens, :output_tokens, :total_tokens,
                    :latency_ms, :status, :retry_count, :error_category
                )
            """)
            await db.execute(stmt, {
                "request_id": request_id,
                "thread_id": thread_id,
                "user_id": user_id,
                "model_name": model_name,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": total_tokens,
                "latency_ms": latency_ms,
                "status": status,
                "retry_count": retry_count,
                "error_category": error_category
            })
            await db.commit()
        except Exception as e:
            logger.error(f"Failed to record request metric: {e}", exc_info=True)

    @staticmethod
    async def get_cost_summary(
        db: AsyncSession,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None
    ) -> Dict[str, Any]:
        """
        Aggregates operational metrics and estimates cost based on configured pricing.
        """
        query = """
            SELECT 
                COUNT(*) as total_requests,
                SUM(CASE WHEN status = 'success' THEN 1 ELSE 0 END) as successful_requests,
                SUM(CASE WHEN status = 'failed' THEN 1 ELSE 0 END) as failed_requests,
                COALESCE(SUM(input_tokens), 0) as total_input_tokens,
                COALESCE(SUM(output_tokens), 0) as total_output_tokens,
                COALESCE(SUM(total_tokens), 0) as total_tokens,
                COALESCE(AVG(latency_ms), 0.0) as average_latency_ms
            FROM request_metrics
            WHERE 1=1
        """
        params: Dict[str, Any] = {}
        if start_date:
            query += " AND created_at >= :start_date"
            params["start_date"] = start_date
        if end_date:
            query += " AND created_at <= :end_date"
            params["end_date"] = end_date

        stmt = text(query)
        result = await db.execute(stmt, params)
        row = result.fetchone()

        total_input = int(row.total_input_tokens or 0)
        total_output = int(row.total_output_tokens or 0)

        # Estimated cost calculation based on configurable rates
        # Formula: (input / 1000 * rate_in) + (output / 1000 * rate_out)
        estimated_input_cost = (total_input / 1000.0) * settings.COST_PER_1K_INPUT_TOKENS
        estimated_output_cost = (total_output / 1000.0) * settings.COST_PER_1K_OUTPUT_TOKENS
        estimated_total_cost = round(estimated_input_cost + estimated_output_cost, 6)

        return {
            "total_requests": int(row.total_requests or 0),
            "successful_requests": int(row.successful_requests or 0),
            "failed_requests": int(row.failed_requests or 0),
            "total_input_tokens": total_input,
            "total_output_tokens": total_output,
            "total_tokens": int(row.total_tokens or 0),
            "average_latency_ms": round(float(row.average_latency_ms or 0.0), 2),
            "estimated_cost_usd": estimated_total_cost,
            "pricing_rates": {
                "per_1k_input_tokens_usd": settings.COST_PER_1K_INPUT_TOKENS,
                "per_1k_output_tokens_usd": settings.COST_PER_1K_OUTPUT_TOKENS
            },
            "date_range": {
                "start_date": start_date,
                "end_date": end_date
            }
        }
