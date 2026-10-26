# pyrefly: ignore[missing-import]
import pytest
import asyncio
from unittest.mock import patch
from httpx import AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text


@pytest.mark.asyncio
async def test_transient_failure_single_retry_then_success(client: AsyncClient):
    """
    Simulates a transient connection failure on the 1st attempt, followed by
    success on the 2nd (retry) attempt. Verifies exactly 1 retry occurred.
    """
    call_count = 0

    async def mock_call_provider(prompt, system_instruction):
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise ConnectionError("Temporary socket connection lost")
        return "NO_API_KEY"

    with patch("app.services.model_service.ModelService._call_provider_api", side_effect=mock_call_provider):
        res = await client.post(
            "/chat",
            headers={"X-User-ID": "EMP001"},
            json={"thread_id": "thread_retry_test", "message": "Show my recent scans"}
        )
        assert res.status_code == 200
        assert "event: done" in res.text
        assert call_count == 2, f"Expected 2 attempts (1 initial + 1 retry), got {call_count}."


@pytest.mark.asyncio
async def test_exhausted_timeout_graceful_error_sse(client: AsyncClient, db_session: AsyncSession):
    """
    Simulates persistent timeouts. Both initial call and retry fail.
    Verifies:
    1. Returns SSE event: error with safe message.
    2. Does not expose stack traces.
    3. Does not record a partial/successful assistant answer in conversation_history.
    4. Records request metric with status='failed'.
    """
    async def mock_timeout(prompt, system_instruction):
        raise asyncio.TimeoutError("Upstream LLM provider did not respond")

    with patch("app.services.model_service.ModelService._call_provider_api", side_effect=mock_timeout):
        res = await client.post(
            "/chat",
            headers={"X-User-ID": "EMP001"},
            json={"thread_id": "thread_timeout_fail", "message": "Show my recent scans"}
        )
        assert res.status_code == 200
        body = res.text
        assert "event: error" in body
        assert "timed out" in body
        assert "Traceback" not in body

        # Verify assistant message was NOT saved in history
        history_rows = (await db_session.execute(
            text("SELECT role FROM conversation_history WHERE thread_id = 'thread_timeout_fail' AND role = 'assistant'")
        )).fetchall()
        assert len(history_rows) == 0, "Failed response must not be recorded as successful assistant message."

        # Verify request_metrics logged failure
        metric_rows = (await db_session.execute(
            text("SELECT status, error_category FROM request_metrics WHERE thread_id = 'thread_timeout_fail'")
        )).fetchall()
        assert len(metric_rows) > 0
        assert metric_rows[0].status == "failed"
        assert metric_rows[0].error_category == "TimeoutError"
