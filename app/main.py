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
    import uvicorn

    uvicorn.run(
        "app.main:main_app",
        host=settings.run.host,
        port=settings.run.port,
        reload=settings.run.reload,
    )