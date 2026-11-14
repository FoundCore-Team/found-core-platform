from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.v1 import api_v1_router
from app.core.config import settings
from app.infrastructure.helpers import analytics_helper, db_helper, redis_helper


@asynccontextmanager
async def lifespan(app: FastAPI):
    yield

    await db_helper.dispose()
    await redis_helper.dispose()
    await analytics_helper.dispose()


main_app = FastAPI(
    lifespan=lifespan,
    title=settings.details.title,
    description=settings.details.description,
)

main_app.include_router(api_v1_router, prefix=settings.api.prefix)

if __name__ == "__main__":
    import copy

    import uvicorn
    from uvicorn.config import LOGGING_CONFIG

    # uvicorn sets up only its own loggers, so INFO of the app (the stub analytics and notification output) was lost.
    # The `app` loggers reuse uvicorn's handler; only in the server, so tests, Celery and scripts are not affected.
    log_config = copy.deepcopy(LOGGING_CONFIG)
    log_config["loggers"]["app"] = {"handlers": ["default"], "level": "INFO"}

    uvicorn.run(
        "app.main:main_app",
        host=settings.run.host,
        port=settings.run.port,
        reload=settings.run.reload,
        log_config=log_config,
    )