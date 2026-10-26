# Root entrypoint redirecting to app.main
from app.main import app

if __name__ == "__main__":
    # pyrefly: ignore[missing-import]
    import uvicorn
    uvicorn.run("app.main:app", host="127.0.0.1", port=8001, reload=True)
