# Data models for your extension

import time
from sqlite3 import Row
from typing import Any, Dict, List, Optional

from pydantic import BaseModel

from .nwcp import NWCServiceProvider


class NWCKey(BaseModel):
    pubkey: str
    wallet: str
    description: str
    expires_at: int
    permissions: str
    created_at: int
    last_used: int

    def get_permissions(self) -> List[str]:
        try:
            return self.permissions.split(" ")
        except Exception:
            return []

    @classmethod
    def from_row(cls, row: Dict[str, Any]) -> "NWCKey":
        return cls(**row)


class OnInvoicePaid(BaseModel):
    class Config:
        arbitrary_types_allowed = True

    sp: NWCServiceProvider
    pubkey: str
    payload: Dict


class NWCBudget(BaseModel):
    id: int
    pubkey: str
    budget_msats: int
    refresh_window: int
    created_at: int
    used_budget_msats: int = 0

    def get_timestamp_range(self) -> tuple[int, int]:
        c = int(time.time())
        if self.refresh_window <= 0:  # never refresh
            # return a timestamp in the future
            return c, c + 21000000
        # calculate the next refresh timestamp
        elapsed = c - self.created_at
        passed_cycles = elapsed // self.refresh_window
        last_cycle = self.created_at + (passed_cycles * self.refresh_window)
        next_cycle = last_cycle + self.refresh_window
        return last_cycle, next_cycle

    @classmethod
    def from_row(cls, row: Row) -> "NWCBudget":
        return cls(**dict(row))


class NWCNewBudget(BaseModel):
    pubkey: Optional[str]
    budget_msats: int
    refresh_window: int
    created_at: int
    

# CRUD models
class CreateNWCKey(BaseModel):
    pubkey: str
    wallet: str
    description: str
    expires_at: int
    permissions: List[str]
    budgets: Optional[List[NWCNewBudget]] = None


class DeleteNWC(BaseModel):
    pubkey: str
    wallet: Optional[str] = None


class GetWalletNWC(BaseModel):
    wallet: Optional[str] = None
    include_expired: Optional[bool] = False


class GetNWC(BaseModel):
    pubkey: str
    wallet: Optional[str] = None
    include_expired: Optional[bool] = False
    refresh_last_used: Optional[bool] = False


class GetBudgetsNWC(BaseModel):
    pubkey: str
    calculate_spent: Optional[bool] = False


class TrackedSpendNWC(BaseModel):
    pubkey: str
    amount_msats: int


# API models
class NWCRegistrationRequest(BaseModel):
    permissions: List[str]
    description: str
    expires_at: int
    budgets: List[NWCNewBudget]


class NWCGetResponse(BaseModel):
    data: NWCKey
    budgets: List[NWCBudget]

