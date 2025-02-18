import asyncio

from fastapi import APIRouter
from lnbits.tasks import create_permanent_unique_task
from loguru import logger

from .crud import db
from .tasks import handle_execution_queue, handle_nwc
from .views import nwcprovider_router
from .views_api import nwcprovider_api_router

nwcprovider_ext: APIRouter = APIRouter(
    prefix="/nwcprovider", tags=["NWC Service Provider"]
)
nwcprovider_ext.include_router(nwcprovider_router)
nwcprovider_ext.include_router(nwcprovider_api_router)

nwcprovider_static_files = [
    {
        "path": "/nwcprovider/static",
        "name": "nwcprovider",
    }
]

scheduled_tasks: list[asyncio.Task] = []


def nwcprovider_stop():
    for task in scheduled_tasks:
        try:
            task.cancel()
        except Exception as ex:
            logger.warning(ex)

def nwcprovider_start():
    task = create_permanent_unique_task("ext_nwcprovider", handle_nwc)
    scheduled_tasks.append(task)
    task = create_permanent_unique_task(
        "ext_nwcprovider_execution_queue", handle_execution_queue
    )
    scheduled_tasks.append(task)


__all__ = [
    "db",
    "nwcprovider_ext",
    "nwcprovider_static_files",
    "nwcprovider_start",
    "nwcprovider_stop",
]
