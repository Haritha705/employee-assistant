import jwt
import logging
from typing import Optional
from datetime import datetime, timezone, timedelta
from fastapi import Header, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import text

from app.config import settings
from app.database.connection import get_db

logger = logging.getLogger(__name__)


class AuthenticatedUser(BaseModel):
    user_id: str
    role: str  # 'employee' or 'manager'
    name: str
    email: str


def create_access_token(user_id: str, role: str, expires_delta: Optional[timedelta] = None) -> str:
    """Generates a signed JWT access token."""
    expire = datetime.now(timezone.utc) + (expires_delta or timedelta(hours=8))
    payload = {
        "sub": user_id,
        "role": role,
        "exp": expire,
        "iat": datetime.now(timezone.utc)
    }
    return jwt.encode(payload, settings.JWT_SECRET_KEY, algorithm=settings.JWT_ALGORITHM)


async def get_current_user(
    authorization: Optional[str] = Header(None, alias="Authorization"),
    x_user_id: Optional[str] = Header(None, alias="X-User-ID"),
    db: AsyncSession = Depends(get_db)
) -> AuthenticatedUser:
    """
    Resolves the authenticated user identity and verified role.
    Role is ALWAYS resolved server-side from the verified database record or signed JWT.
    Arbitrary user-provided roles are strictly rejected.
    """
    user_id = None
    
    # Check Bearer JWT token first
    if authorization and authorization.startswith("Bearer "):
        token = authorization[7:].strip()
        try:
            payload = jwt.decode(token, settings.JWT_SECRET_KEY, algorithms=[settings.JWT_ALGORITHM])
            user_id = payload.get("sub")
        except jwt.PyJWTError as e:
            logger.warning(f"Invalid JWT token: {e}")
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid or expired authentication token"
            )
    elif x_user_id:
        user_id = x_user_id.strip()

    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide Authorization Bearer token or valid X-User-ID header."
        )

    # Validate against database to guarantee existence and canonical server-enforced role
    stmt = text("SELECT id, name, email, role FROM employees WHERE id = :user_id")
    result = await db.execute(stmt, {"user_id": user_id})
    row = result.fetchone()

    if not row:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"User identity '{user_id}' does not exist in authorized directory."
        )

    return AuthenticatedUser(
        user_id=row.id,
        name=row.name,
        email=row.email,
        role=row.role
    )


async def require_manager(user: AuthenticatedUser = Depends(get_current_user)) -> AuthenticatedUser:
    """Enforces manager-only access."""
    if user.role != "manager":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Forbidden: Manager privileges required."
        )
    return user
