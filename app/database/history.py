import logging
from typing import List, Dict
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text
from fastapi import HTTPException, status

logger = logging.getLogger(__name__)


class HistoryService:
    @staticmethod
    async def verify_thread_ownership(db: AsyncSession, thread_id: str, user_id: str):
        """
        Guarantees that a thread belongs to the authenticated user.
        If the thread has messages owned by another user, raises HTTP 403 Forbidden.
        """
        stmt = text("SELECT DISTINCT user_id FROM conversation_history WHERE thread_id = :thread_id LIMIT 1")
        result = await db.execute(stmt, {"thread_id": thread_id})
        owner_id = result.scalar()

        if owner_id and owner_id != user_id:
            logger.warning(f"Security Alert: User {user_id} attempted to access thread {thread_id} owned by {owner_id}")
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Access denied: Conversation thread belongs to another user."
            )

    @classmethod
    async def get_history(cls, db: AsyncSession, thread_id: str, user_id: str, limit: int = 10) -> List[Dict[str, str]]:
        """
        Retrieves recent conversation history for the thread, scoped to the user.
        """
        await cls.verify_thread_ownership(db, thread_id, user_id)
        
        stmt = text("""
            SELECT role, content
            FROM conversation_history
            WHERE thread_id = :thread_id AND user_id = :user_id
            ORDER BY id ASC
            LIMIT :limit
        """)
        result = await db.execute(stmt, {"thread_id": thread_id, "user_id": user_id, "limit": limit})
        rows = result.fetchall()
        return [{"role": r.role, "content": r.content} for r in rows]

    @classmethod
    async def add_message(cls, db: AsyncSession, thread_id: str, user_id: str, role: str, content: str):
        """
        Persists a completed message (user or assistant) to conversation history.
        """
        await cls.verify_thread_ownership(db, thread_id, user_id)
        
        stmt = text("""
            INSERT INTO conversation_history (thread_id, user_id, role, content)
            VALUES (:thread_id, :user_id, :role, :content)
        """)
        await db.execute(stmt, {
            "thread_id": thread_id,
            "user_id": user_id,
            "role": role,
            "content": content
        })
        await db.commit()
