import logging
from fastapi import APIRouter, Depends
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from app.auth import AuthenticatedUser, get_current_user
from app.database.connection import get_db
from app.database.history import HistoryService
from app.services.rate_limiter import check_rate_limit
from app.agent.assistant import OpsAssistant

logger = logging.getLogger(__name__)
router = APIRouter(tags=["Chat"])

assistant = OpsAssistant()


class ChatRequest(BaseModel):
    thread_id: str = Field(..., min_length=1, max_length=100, description="Unique conversation thread identifier")
    message: str = Field(..., min_length=1, description="User's natural language operational query")


@router.post("/chat")
async def chat_endpoint(
    request: ChatRequest,
    user: AuthenticatedUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db)
):
    """
    POST /chat: Streams natural-language answers about factory operations via Server-Sent Events (SSE).
    Enforces authentication, role-based data isolation, thread security, and optional rate limiting.
    """
    # 1. Enforce optional Redis rate limiting (returns HTTP 429 before streaming begins)
    await check_rate_limit(user.user_id)

    # 2. Enforce thread ownership validation before streaming begins (returns HTTP 403 if thread belongs to another user)
    await HistoryService.verify_thread_ownership(db, request.thread_id, user.user_id)

    stream_generator = assistant.handle_chat_stream(
        thread_id=request.thread_id,
        message=request.message,
        user=user,
        db=db
    )

    return StreamingResponse(
        stream_generator,
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no"
        }
    )
