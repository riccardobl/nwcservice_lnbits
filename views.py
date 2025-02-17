from fastapi import APIRouter, Depends, Request
from lnbits.core.models import User
from lnbits.decorators import check_admin, check_user_exists
from lnbits.helpers import template_renderer
from starlette.responses import HTMLResponse

nwcprovider_router = APIRouter()


def nwcprovider_renderer():
    return template_renderer(["nwcprovider/templates"])


@nwcprovider_router.get("/", response_class=HTMLResponse)
async def index(request: Request, user: User = Depends(check_user_exists)):
    return nwcprovider_renderer().TemplateResponse(
        "nwcprovider/index.html", {"request": request, "user": user.json()}
    )


@nwcprovider_router.get("/admin", response_class=HTMLResponse)
async def admin(request: Request, user: User = Depends(check_admin)):
    return nwcprovider_renderer().TemplateResponse(
        "nwcprovider/admin.html", {"request": request, "user": user.json()}
    )
