from http import HTTPStatus
from typing import Dict, List, Optional

import secp256k1
from fastapi import APIRouter, Depends, Request, Query
from fastapi.responses import JSONResponse
from lnbits.core.models import WalletTypeInfo
from lnbits.decorators import check_admin, require_admin_key

from .crud import (
    create_nwc,
    delete_nwc,
    get_all_config_nwc,
    get_budgets_nwc,
    get_config_nwc,
    get_nwc,
    get_wallet_nwcs,
    set_config_nwc,
)
from .models import (
    NWCRegistrationRequest,
    GetBudgetsNWC,
    GetWalletNWC,
    NWCGetResponse,
    CreateNWCKey,
    DeleteNWC,
    GetNWC,
    GetBudgetsNWC
)
from .permission import nwc_permissions

nwcprovider_api_router = APIRouter()


# Get supported permissions
@nwcprovider_api_router.get(
    "/api/v1/permissions", 
    status_code=HTTPStatus.OK
)
async def api_get_permissions() -> Dict:
    return nwc_permissions


## Get nwc keys associated with the wallet
@nwcprovider_api_router.get(
    "/api/v1/nwc", 
    status_code=HTTPStatus.OK, 
    response_model=List[NWCGetResponse]
)
async def api_get_nwcs(
    include_expired: bool = False,
    calculate_spent_budget: bool = False,
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    wallet_id = wallet.wallet.id
    wallet_nwcs = GetWalletNWC(
        wallet=wallet_id, 
        include_expired=include_expired
    )
    nwcs = await get_wallet_nwcs(wallet_nwcs)
    out = []
    for nwc in nwcs:
        budgets_nwc = GetBudgetsNWC(
            pubkey=nwc.pubkey, 
            calculate_spent=calculate_spent_budget
        )
        budgets = await get_budgets_nwc(budgets_nwc)
        res = NWCGetResponse(data=nwc, budgets=budgets)
        out.append(res)
    return out


# Get a nwc key
@nwcprovider_api_router.get(
    "/api/v1/nwc/{pubkey}", 
    status_code=HTTPStatus.OK, 
    response_model=NWCGetResponse
)
async def api_get_nwc(
    pubkey: str,
    include_expired: Optional[bool] = False,
    wallet: WalletTypeInfo = Depends(require_admin_key)
) -> NWCGetResponse:
    wallet_id = wallet.wallet.id
    nwc = await get_nwc(GetNWC(pubkey=pubkey, wallet=wallet_id, include_expired=include_expired))
    if not nwc:
        raise Exception("Pubkey has no associated wallet")
    res = NWCGetResponse(data=nwc, budgets=await get_budgets_nwc(
        GetBudgetsNWC(
            pubkey=pubkey
        )
    ))
    return res


# Get pairing url for given secret
@nwcprovider_api_router.get(
    "/api/v1/pairing/{secret}", 
    status_code=HTTPStatus.OK, 
    response_model=str
)
async def api_get_pairing_url(
    req: Request, 
    secret: str
) -> str:
    pprivkey: Optional[str] = await get_config_nwc("provider_key")
    if not pprivkey:
        raise Exception("Extension is not configured")
    relay = await get_config_nwc("relay")
    if not relay:
        raise Exception("Extension is not configured")
    relay_alias: Optional[str] = await get_config_nwc("relay_alias")
    if relay_alias:
        relay = relay_alias
    else:
        if relay == "nostrclient":
            scheme = req.url.scheme  # http or https
            netloc = req.url.netloc  # hostname and port
            if scheme == "http":
                scheme = "ws"
            else:
                scheme = "wss"
            netloc += "/nostrclient/api/v1/relay"
            relay = f"{scheme}://{netloc}"
    psk = secp256k1.PrivateKey(bytes.fromhex(pprivkey))
    ppk = psk.pubkey
    if not ppk:
        raise Exception("Error generating pubkey")
    ppubkey = ppk.serialize().hex()[2:]
    url = "nostr+walletconnect://"
    url += ppubkey
    url += "?relay=" + relay
    url += "&secret=" + secret
    # lud16=?
    return url


## Register a new nwc key
@nwcprovider_api_router.put(
    "/api/v1/nwc/{pubkey}",
    status_code=HTTPStatus.CREATED,
    response_model=NWCGetResponse,
)
async def api_register_nwc(
    pubkey: str,
    data: NWCRegistrationRequest, 
    wallet: WalletTypeInfo = Depends(require_admin_key),
):
    wallet_id = wallet.wallet.id
    nwc = await create_nwc(
        CreateNWCKey(
            pubkey=pubkey,
            wallet=wallet_id,
            description=data.description,
            expires_at=data.expires_at,
            permissions=data.permissions,
            budgets=data.budgets,
        )
    )
    budgets = await get_budgets_nwc(
        GetBudgetsNWC(
            pubkey=pubkey
        )
    )
    res = NWCGetResponse(data=nwc, budgets=budgets)
    return res


# Delete a nwc key
@nwcprovider_api_router.delete(
    "/api/v1/nwc/{pubkey}", 
    status_code=HTTPStatus.OK
)
async def api_delete_nwc(
    pubkey: str,
    wallet: WalletTypeInfo = Depends(require_admin_key)
):
    wallet_id = wallet.wallet.id
    await delete_nwc(DeleteNWC(
        pubkey=pubkey, 
        wallet=wallet_id
    ))
    return JSONResponse(
        content={"message": f"NWC key {pubkey} deleted successfully."}
    )


# Get config
@nwcprovider_api_router.get(
    "/api/v1/config", 
    status_code=HTTPStatus.OK, 
    dependencies=[Depends(check_admin)]
)
async def api_get_all_config_nwc():
    config = await get_all_config_nwc()
    return config


# Get config
@nwcprovider_api_router.get(
    "/api/v1/config/{key}",
    status_code=HTTPStatus.OK,
    dependencies=[Depends(check_admin)],
)
async def api_get_config_nwc(key: str):
    config = await get_config_nwc(key)
    out = {}
    out[key] = config
    return out


# Set config
@nwcprovider_api_router.post(
    "/api/v1/config", 
    status_code=HTTPStatus.OK, 
    dependencies=[Depends(check_admin)]
)
async def api_set_config_nwc(req: Request):
    data = await req.json()
    for key, value in data.items():
        await set_config_nwc(key, value)
    return await api_get_all_config_nwc()
