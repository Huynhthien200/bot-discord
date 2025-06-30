#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, types, json, logging, asyncio, httpx
from aiohttp import web
sys.modules["audioop"] = types.ModuleType("audioop")

import discord
from discord.ext import commands, tasks
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair

# ─── ENV CONFIG ───────────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING  = os.getenv("SUI_PRIVATE_KEY", "")
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS", "")
RPC_URL         = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
POLL_INTERVAL   = float(os.getenv("POLL_INTERVAL", "1"))

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu biến môi trường bắt buộc")

# ─── LOAD WATCHED ADDRESSES ───────────────────────────────────────
try:
    with open("watched.json", encoding="utf-8") as f:
        WATCHED = json.load(f)
except Exception as e:
    logging.error("Lỗi đọc watched.json: %s", e)
    WATCHED = []

# ─── LOAD SUI KEY ─────────────────────────────────────────────────
import base64
from bech32 import bech32_decode, convertbits

def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        hrp, data = bech32_decode(raw)
        if hrp != "suiprivkey" or not data:
            raise ValueError("Invalid HRP or data")
        key_bytes = bytes(convertbits(data, 5, 8, False))
        key_b64 = base64.b64encode(key_bytes).decode("ascii")
        return SuiKeyPair.from_b64(key_b64)
    if hasattr(SuiKeyPair, "from_any"):
        return SuiKeyPair.from_any(raw)
    return SuiKeyPair.from_b64(raw)

# ─── INIT SUI CLIENT ──────────────────────────────────────────────
keypair = load_keypair(SUI_KEY_STRING)
cfg = SuiConfig.user_config(rpc_url=RPC_URL, prv_keys=[SUI_KEY_STRING])
client = SyncClient(cfg)
SENDER = str(cfg.active_address)

# ─── DISCORD SETUP ────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

http_client = httpx.AsyncClient(timeout=10)
balance_cache = {}

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as exc:
        logging.warning("Failed to send message to Discord: %s", exc)

async def get_balance(addr: str) -> int | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "suix_getBalance", "params": [addr]}
    try:
        r = await http_client.post(RPC_URL, json=payload)
        r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as exc:
        logging.warning("Failed to get balance: %s", exc)
        return None

def withdraw_all() -> str | None:
    try:
        coins = client.get_gas(address=SENDER)
        if not coins:
            return None
        resp = client.transfer_sui(
            signer=keypair,
            sui_object_id=coins[0].id,
            recipient=TARGET_ADDRESS,
            amount=None,
        )
        if resp and resp.effects.status.status == "success":
            return resp.digest
    except Exception as exc:
        logging.error("Withdraw failed: %s", exc)
    return None

@tasks.loop(seconds=POLL_INTERVAL)
async def tracker():
    for entry in WATCHED:
        name = entry.get("name", "Unnamed")
        addr = entry.get("address", "").lower()
        can_withdraw = entry.get("withdraw", False)

        if not addr:
            continue

        cur = await get_balance(addr)
        if cur is None:
            continue

        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta = (cur - prev) / 1e9
            arrow = "🟢" if delta > 0 else "🔴"

            await discord_send(
                f"💼 **{name}** {arrow} thay đổi **{abs(delta):.4f} SUI** ({prev/1e9:.4f} → {cur/1e9:.4f})"
            )

            if delta > 0 and can_withdraw:
                tx = withdraw_all()
                if tx:
                    await discord_send(
                        f"💸 Đã rút toàn bộ về `{TARGET_ADDRESS[:10]}...` · Tx `{tx}`"
                    )

        balance_cache[addr] = cur

@bot.event
async def on_ready():
    tracker.start()
    bot.loop.create_task(start_web())
    logging.info("Logged in as %s", bot.user)

 watched_list = "\n".join([
    f"- {entry['name']}: {entry['address']} {'(Auto-rút)' if entry.get('withdraw') else ''}"
    for entry in WATCHED
])
sender_info = f"🔑 Ví có private key (SENDER): `{SENDER}`"

await discord_send(f"{sender_info}\n🛰️ Bot đang theo dõi:\n{watched_list}")

@bot.command()
async def ping(ctx):
    await ctx.send("✅ Pong")

@bot.command()
async def balances(ctx):
    lines = []
    for entry in WATCHED:
        name = entry.get("name", "Unnamed")
        addr = entry.get("address", "")
        bal = await get_balance(addr)
        if bal is not None:
            lines.append(f"{name}: {bal/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) if lines else "RPC lỗi")

async def handle(_):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    bot.run(DISCORD_TOKEN)
