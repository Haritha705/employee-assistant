import logging
from typing import Optional
from fastapi import HTTPException, status
from app.config import settings

logger = logging.getLogger(__name__)

_redis_client = None
_redis_available = None


def get_redis_client():
    global _redis_client, _redis_available
    if _redis_available is False:
        return None
    if _redis_client is not None:
        return _redis_client

    if not settings.REDIS_URL:
        _redis_available = False
        return None

    try:
        # pyrefly: ignore[missing-import]
        import redis
        client = redis.Redis.from_url(settings.REDIS_URL, decode_responses=True, socket_timeout=1.0)
        client.ping()
        _redis_client = client
        _redis_available = True
        logger.info("Connected to Redis for rate limiting.")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis rate limiter unavailable ({e}). Running in fallback mode (fail-open).")
        _redis_available = False
        return None


async def check_rate_limit(user_id: str):
    """
    Applies per-user rate limiting using authenticated identity.
    If Redis is unavailable or unconfigured, gracefully allows requests (fail-open).
    """
    client = get_redis_client()
    if client is None:
        return

    try:
        key = f"rate_limit:{user_id}"
        current = client.incr(key)
        if current == 1:
            client.expire(key, settings.RATE_LIMIT_WINDOW_SECONDS)

        if current > settings.RATE_LIMIT_REQUESTS:
            logger.warning(f"Rate limit exceeded for user: {user_id}")
            raise HTTPException(
                status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                detail=f"Rate limit exceeded ({settings.RATE_LIMIT_REQUESTS} requests per {settings.RATE_LIMIT_WINDOW_SECONDS}s). Please wait."
            )
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"Error checking rate limit in Redis: {e}")
        # Fail-open
        return
