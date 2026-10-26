import logging
from contextlib import asynccontextmanager
from fastapi import FastAPI, Depends
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.config import settings
from app.database.connection import init_db, get_db, engine
from app.routes.chat import router as chat_router
from app.routes.metrics import router as metrics_router

# Configure logging
logging.basicConfig(
    level=settings.LOG_LEVEL,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s"
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(f"Starting {settings.APP_NAME}...")
    try:
        await init_db()
        logger.info("Database initialized and verified successfully.")
    except Exception as e:
        logger.warning(
            f"Database auto-initialization could not complete at startup: {e}. "
            "Please ensure PostgreSQL is accessible or configure a valid DATABASE_URL."
        )
    yield
    logger.info("Shutting down application...")
    await engine.dispose()


app = FastAPI(
    title=settings.APP_NAME,
    description="Factory Ops Assistant Service providing role-isolated data queries via SSE streaming.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router)
app.include_router(metrics_router)


@app.get("/health", tags=["Health"])
async def health_check(db: AsyncSession = Depends(get_db)):
    """Health check endpoint verifying database connectivity."""
    db_status = "connected"
    try:
        await db.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"

    return {
        "status": "healthy" if "unhealthy" not in db_status else "degraded",
        "database": db_status,
        "model_provider": settings.MODEL_NAME,
        "rate_limiting": "enabled" if settings.REDIS_URL else "disabled (optional)"
    }
