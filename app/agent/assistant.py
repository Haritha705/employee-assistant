import json
import logging
import uuid
from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthenticatedUser
from app.database.history import HistoryService
from app.database.metrics import MetricsService
from app.services.model_service import ModelService, ModelExecutionResult

logger = logging.getLogger(__name__)


class OpsAssistant:
    def __init__(self):
        self.model_service = ModelService()

    async def handle_chat_stream(
        self,
        thread_id: str,
        message: str,
        user: AuthenticatedUser,
        db: AsyncSession
    ) -> AsyncGenerator[str, None]:
        """
        Manages the chat conversation lifecycle with SSE event formatting.
        - Verifies thread ownership (prevents thread manipulation/cross-user hijacking)
        - Records user message
        - Streams response chunks as SSE
        - Persists assistant response upon successful completion only
        - Logs tokens and latency metrics
        """
        request_id = str(uuid.uuid4())
        
        # 1. Verify thread ownership and fetch previous history
        history = await HistoryService.get_history(db, thread_id, user.user_id, limit=10)

        # 2. Persist the incoming user message
        await HistoryService.add_message(db, thread_id, user.user_id, "user", message)

        accumulated_text = []
        final_result: ModelExecutionResult = None

        try:
            async for event in self.model_service.stream_chat(message, history, user, db):
                ev_type = event.get("type")

                if ev_type == "chunk":
                    chunk_text = event.get("content", "")
                    accumulated_text.append(chunk_text)
                    payload = json.dumps({"content": chunk_text})
                    yield f"event: message\ndata: {payload}\n\n"

                elif ev_type == "error":
                    final_result = event.get("result")
                    err_msg = event.get("error", "An error occurred during response generation.")
                    payload = json.dumps({"error": err_msg})
                    yield f"event: error\ndata: {payload}\n\n"

                elif ev_type == "final":
                    final_result = event.get("result")

            # 3. Handle stream completion
            if final_result and final_result.status == "success":
                full_reply = "".join(accumulated_text).strip()
                # Store completed response in history
                await HistoryService.add_message(db, thread_id, user.user_id, "assistant", full_reply)

                done_payload = json.dumps({
                    "thread_id": thread_id,
                    "total_tokens": final_result.total_tokens,
                    "latency_ms": final_result.latency_ms
                })
                yield f"event: done\ndata: {done_payload}\n\n"

            # 4. Record Metrics (success or failure)
            if final_result:
                await MetricsService.record_metric(
                    db=db,
                    request_id=request_id,
                    thread_id=thread_id,
                    user_id=user.user_id,
                    model_name=self.model_service.model_name,
                    input_tokens=final_result.input_tokens,
                    output_tokens=final_result.output_tokens,
                    total_tokens=final_result.total_tokens,
                    latency_ms=final_result.latency_ms,
                    status=final_result.status,
                    retry_count=final_result.retry_count,
                    error_category=final_result.error_category
                )

        except Exception as e:
            logger.error(f"Unexpected error in assistant stream: {e}", exc_info=True)
            err_payload = json.dumps({"error": "An unexpected error occurred during streaming."})
            yield f"event: error\ndata: {err_payload}\n\n"

            # Record failed metric
            await MetricsService.record_metric(
                db=db,
                request_id=request_id,
                thread_id=thread_id,
                user_id=user.user_id,
                model_name=self.model_service.model_name,
                input_tokens=None,
                output_tokens=None,
                total_tokens=None,
                latency_ms=0.0,
                status="failed",
                retry_count=0,
                error_category=type(e).__name__
            )
