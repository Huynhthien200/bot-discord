#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, types, logging, httpx
from aiohttp import web

sys.modules["audioop"] = types.ModuleType("audioop")

import discord
from discord.ext import commands, tasks
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s")

DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
CHANNEL_ID     = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu biến môi trường bắt buộc")

RPCS = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
RPC_IDX = 0

def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey") and hasattr(SuiKeyPair, "from_keystring"):
        return SuiKeyPair.from_keystring(raw)
    if hasattr(SuiKeyPair, "from_any"):
        return SuiKeyPair.from_any(raw)
    return SuiKeyPair.from_b64(raw)

keypair       = load_keypair(SUI_KEY_STRING)
cfg           = SuiConfig.user_config(rpc_url=RPCS[RPC_IDX], prv_keys=[SUI_KEY_STRING])
client        = SyncClient(cfg)
SENDER_ADDR   = str(cfg.active_address).lower()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}
http_client = httpx.AsyncClient(timeout=10.0)

async def discord_send(msg: str):
    ch = await bot.fetch_channel(CHANNEL_ID)
    await ch.send(msg)

async def get_balance(addr: str) -> int | None:
    payload = {"jsonrpc":"2.0","id":1,"method":"suix_getBalance","params":[addr]}
    try:
        r = await http_client.post(RPCS[RPC_IDX], json=payload)
        r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as exc:
        logging.warning("Lỗi RPC: %s", exc)
        return None

def first_gas_coin(owner: str) -> str | None:
    res = client.get_coins(owner)
    if res and res.data and res.data.data:
        return res.data.data[0].coin_object_id
    return None

def withdraw_all() -> str | None:
    try:
        coin_id = first_gas_coin(SENDER_ADDR)
        if not coin_id:
            return None
        tx = SuiTransaction(client=client, initial_sender=keypair)
        tx.transfer_sui(recipient=TARGET_ADDRESS,
                        from_coin=coin_id,
                        amount=None)
        res = tx.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as exc:
        logging.error("withdraw_all thất bại: %s", exc)
    return None

@tasks.loop(seconds=1)
async def tracker():
    for name, addr in WATCHED.items():
        cur = await get_balance(addr)
        if cur is None:
            continue
        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta = (cur - prev) / 1e9
            arrow = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            await discord_send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI"
            )
            if delta > 0 and addr.lower() == SENDER_ADDR:
                tx = withdraw_all()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về `{TARGET_ADDRESS[:6]}…`"
                        f"\n🔗 Tx: `{tx}`"
                    )
        balance_cache[addr] = cur

@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logging.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx): await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):
    lines = []
    for name, addr in WATCHED.items():
        b = await get_balance(addr)
        if b is not None:
            lines.append(f"💰 {name}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

async def handle_ping(_): return web.Response(text="✅ Alive")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080"))).start()

if __name__ == "__main__":
    WATCHED = {
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
