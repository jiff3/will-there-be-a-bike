from fastapi import APIRouter

from app.modules.auth.router import router as auth_router
from app.modules.commutes.router import router as rider_router
from app.modules.health.router import router as health_router
from app.modules.ingestion.router import router as stations_router
from app.modules.ml.router import router as ml_router
from app.modules.monitoring.router import router as monitoring_router
from app.modules.recommendations.router import router as recommendations_router
from app.modules.replay.router import router as replay_router
from app.modules.stations.router import router as catalog_router

api_router = APIRouter()
api_router.include_router(health_router, prefix="/health", tags=["health"])
api_router.include_router(stations_router, prefix="/stations", tags=["stations"])
api_router.include_router(catalog_router, prefix="/stations", tags=["stations"])
api_router.include_router(ml_router, prefix="/forecasts", tags=["forecasts"])
api_router.include_router(auth_router, prefix="/auth", tags=["accounts"])
api_router.include_router(rider_router, tags=["saved commutes and notifications"])
api_router.include_router(replay_router, prefix="/replay", tags=["historical demand replay"])
api_router.include_router(
    recommendations_router, prefix="/recommendations", tags=["recommendations"]
)
api_router.include_router(monitoring_router, tags=["operations"])
