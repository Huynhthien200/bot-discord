#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import httpx
import types, sys
sys.modules['audioop'] = types.ModuleType('audioop')

from aiohttp import web
import discord
from discord.ext import commands, tasks

from pysui import SyncClient
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s")

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
RPC_ENV = os.getenv("SUI_RPC_LIST", "")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu DISCORD_TOKEN, DISCORD_CHANNEL_ID, SUI_PRIVATE_KEY hoặc SUI_TARGET_ADDRESS")

def load_keypair(keystr: str) -> SuiKeyPair:
    keystr = keystr.strip()
    if keystr.startswith("suiprivkey") and hasattr(SuiKeyPair, "from_bech32"):
        return SuiKeyPair.from_bech32(keystr)
    if hasattr(SuiKeyPair, "from_any"):
        return SuiKeyPair.from_any(keystr)
    return SuiKeyPair.from_b64(keystr)

keypair = load_keypair(SUI_KEY_STRING)

rpc_list = [r.strip() for r in RPC_ENV.split(",") if r.strip()] or [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
rpc_index = 0
client = SyncClient(rpc_list[rpc_index])
http_client = httpx.AsyncClient(timeout=10.0)

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}

def rotate_rpc() -> None:
    global rpc_index, client
    rpc_index = (rpc_index + 1) % len(rpc_list)
    try:
        client.close()
    except Exception:
        pass
    client = SyncClient(rpc_list[rpc_index])

async def get_balance(addr: str) -> int | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "suix_getBalance", "params": [addr]}
    for _ in range(len(rpc_list)):
        try:
            r = await http_client.post(rpc_list[rpc_index], json=payload)
            r.raise_for_status()
            return int(r.json()["result"]["totalBalance"])
        except Exception:
            rotate_rpc()
    return None

def send_all_sui() -> str | None:
    for _ in range(len(rpc_list)):
        try:
            tx = SuiTransaction(client, initial_sender=keypair)
            tx.transfer_sui(recipient=TARGET_ADDRESS)
            res = tx.execute()
            if res.effects.status.status == "success":
                return res.tx_digest
        except Exception:
            rotate_rpc()
    return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception:
        pass

@tasks.loop(seconds=10)
async def tracker():
    for name, addr in watched_accounts.items():
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
            if delta > 0 and addr.lower() == keypair.public_key.as_sui_address.lower():
                tx = send_all_sui()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về `{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`"
                    )
        balance_cache[addr] = cur

@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()

@bot.command()
async def ping(ctx):
    await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):
    lines = []
    for name, addr in watched_accounts.items():
        b = await get_balance(addr)
        if b is not None:
            lines.append(f"💰 {name}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

async def handle_ping(_):
    return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

if __name__ == "__main__":
    watched_accounts = {
        "Neuter": "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng": "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
