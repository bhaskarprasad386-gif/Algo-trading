from fastapi import FastAPI

app = FastAPI(
    title="Algo Trading Platform",
    version="0.1.0",
)


@app.get("/health")
def health():
    return {
        "status": "ok",
        "service": "algo-trading-backend",
        "version": "0.1.0",
    }


@app.get("/api/v1/status")
def api_status():
    return {
        "status": "connected",
        "service": "algo-trading-backend",
        "api": "v1",
    }
