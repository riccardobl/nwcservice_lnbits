import time
from typing import List, Optional

from lnbits.db import Database

from .execution_queue import enqueue
from .models import (
    CreateNWCKey,
    DeleteNWC,
    GetBudgetsNWC,
    GetWalletNWC,
    NWCBudget,
    NWCKey,
    TrackedSpendNWC,
    GetNWC,
    NWCNewBudget
)

db = Database("ext_nwcprovider")


async def create_nwc(data: CreateNWCKey) -> NWCKey:
    nwckey_entry = NWCKey(
        pubkey=data.pubkey,
        wallet=data.wallet,
        description=data.description,
        expires_at=int(data.expires_at) if data.expires_at else 0,
        permissions=" ".join(data.permissions),
        created_at=int(time.time()),
        last_used=int(time.time()),
    )
    await db.insert("nwcprovider.keys", nwckey_entry)
    if data.budgets:
        for budget in data.budgets:
            budget_entry = NWCNewBudget(  # fixme
                pubkey=data.pubkey,
                budget_msats=budget.budget_msats,
                refresh_window=budget.refresh_window,
                created_at=budget.created_at,
            )
            await db.insert("nwcprovider.budgets", budget_entry)
    return NWCKey(**nwckey_entry.dict())


async def delete_nwc(data: DeleteNWC) -> None:
    await db.execute(
        "DELETE FROM nwcprovider.keys WHERE pubkey = :pubkey AND wallet = :wallet",
        {"pubkey": data.pubkey, "wallet": data.wallet},
    )


async def get_wallet_nwcs(data: GetWalletNWC) -> List[NWCKey]:
    return await db.fetchall(
        """
        SELECT * FROM nwcprovider.keys
        WHERE wallet = :wallet AND (expires_at = 0 OR expires_at > :expires)
        """,
        {
            "wallet": data.wallet,
            "expires": int(time.time()) if not data.include_expired else -1,
        },
        model=NWCKey,
    )


async def get_nwc(data: GetNWC) -> Optional[NWCKey]:
    # expires_at = 0 means it never expires
    if data.wallet:
        row = await db.fetchone(
            """
            SELECT * FROM nwcprovider.keys
            WHERE pubkey = :pubkey AND wallet = :wallet
            AND (expires_at = 0 OR expires_at > :expires)
            """,
            {
                "pubkey": data.pubkey,
                "wallet": data.wallet,
                "expires": int(time.time()) if not data.include_expired else -1,
            },
            NWCKey,
        )
    else:
        row = await db.fetchone(
            """
            SELECT * FROM nwcprovider.keys
            WHERE pubkey = ? AND (expires_at = 0 OR expires_at > ?)
            """,
            (data.pubkey, int(time.time()) if not data.include_expired else -1),
        )
        row = await db.fetchone(
            """
            SELECT * FROM nwcprovider.keys
            WHERE pubkey = :pubkey AND (expires_at = 0 OR expires_at > :expires)
            """,
            {
                "pubkey": data.pubkey,
                "expires": int(time.time()) if not data.include_expired else -1,
            },
            NWCKey,
        )
    if not row:
        return None
    if data.refresh_last_used:
        await db.execute(
            """
            UPDATE nwcprovider.keys SET last_used =
            :last_used WHERE pubkey = :pubkey
            """,
            {"last_used": int(time.time()), "pubkey": data.pubkey},
        )
    return NWCKey(**row)


async def get_budgets_nwc(data: GetBudgetsNWC) -> Optional[NWCBudget]:
    rows = await db.fetchall(
        "SELECT * FROM nwcprovider.budgets WHERE pubkey = :pubkey",
        {"pubkey": data.pubkey},
    )
    budgets = [NWCBudget(**row) for row in rows]
    if data.calculate_spent:
        for budget in budgets:
            last_cycle, next_cycle = budget.get_timestamp_range()
            tot_spent_in_range_msats = await db.fetchone(
                """
                SELECT SUM(amount_msats) FROM nwcprovider.spent
                WHERE pubkey = :pubkey AND created_at >=
                :last_cycle AND created_at < :next_cycle
                """,
                {
                    "pubkey": data.pubkey,
                    "last_cycle": last_cycle,
                    "next_cycle": next_cycle,
                },
            )           
            tot_spent_in_range_msats = next(iter(tot_spent_in_range_msats.values())) or 0
            budget.used_budget_msats = tot_spent_in_range_msats
    return budgets


async def tracked_spend_nwc(data: TrackedSpendNWC, action):
    async def r():
        created_at = int(time.time())
        budgets = await get_budgets_nwc(GetBudgetsNWC(
            pubkey=data.pubkey
        ))
        in_budget = True
        for budget in budgets:
            last_cycle, next_cycle = budget.get_timestamp_range()
            tot_spent_in_range_msats = (
                next(iter((await db.fetchone(
                    """
                    SELECT SUM(amount_msats) FROM nwcprovider.spent
                    WHERE pubkey = :pubkey AND created_at >=
                    :last_cycle AND created_at < :next_cycle
                    """,
                    {
                        "pubkey": data.pubkey,
                        "last_cycle": last_cycle,
                        "next_cycle": next_cycle,
                    },
                )).values())) or 0
            )
            if tot_spent_in_range_msats + data.amount_msats > budget.budget_msats:
                in_budget = False
                break
        if not in_budget:
            return False, None
        out = await action()
        await db.execute(
            """
            INSERT INTO nwcprovider.spent (pubkey, amount_msats, created_at)
            VALUES (:pubkey, :amount_msats, :created_at)
            """,
            {
                "pubkey": data.pubkey,
                "amount_msats": data.amount_msats,
                "created_at": created_at,
            },
        )
        return True, out
    
    return await enqueue(r)


async def get_config_nwc(key: str):
    row = await db.fetchone(
        "SELECT * FROM nwcprovider.config WHERE key = :key", {"key": key}
    )
    if not row:
        return None
    return row["value"]


async def set_config_nwc(key: str, value: str):
    await db.execute(
        """
        INSERT OR REPLACE INTO nwcprovider.config (key, value)
        VALUES (:key, :value)
        """,
        {"key": key, "value": value},
    )


async def get_all_config_nwc():
    rows = await db.fetchall("SELECT * FROM nwcprovider.config")
    return {row["key"]: row["value"] for row in rows}
