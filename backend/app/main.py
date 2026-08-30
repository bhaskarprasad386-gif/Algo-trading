from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
from app.core.logger import app_logger
from app.routes import market_data


@asynccontextmanager
async def lifespan(app: FastAPI):
    app_logger.info("Application starting...")
    yield
    app_logger.info("Application shutting down...")


app = FastAPI(
    title="Algo Trading Backend",
    description="Market data and trading API",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(market_data.router)


@app.get("/")
async def root():
    return {"message": "Welcome to Algo Trading API", "status": "running"}


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "Algo Trading Backend"}


@app.get("/routes")
async def list_routes():
    routes = []
    for route in app.routes:
        routes.append({
            "path": route.path,
            "methods": list(route.methods) if hasattr(route, "methods") else []
        })
    return {"total_routes": len(routes), "routes": sorted(routes, key=lambda x: x["path"])}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=True)
