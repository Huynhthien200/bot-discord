#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, json, logging, asyncio, base64
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair

# ─── ENVIRONMENT VARIABLES ───
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN", "")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING  = os.getenv("SUI_PRIVATE_KEY", "")
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS", "")
RPC_URL         = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
POLL_INTERVAL   = float(os.getenv("POLL_INTERVAL", "5"))

# ─── CHECK ENV ───
if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("⚠️ Thiếu biến môi trường bắt buộc")

# ─── LOAD KEYPAIR ───
from bech32 import bech32_decode, convertbits
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        hrp, data = bech32_decode(raw)
        if hrp != "suiprivkey" or not data:
            raise ValueError("Invalid suiprivkey")
        key_bytes = bytes(convertbits(data, 5, 8, False))
        key_b64 = base64.b64encode(key_bytes).decode("ascii")
        return SuiKeyPair.from_b64(key_b64)
    return SuiKeyPair.from_any(raw)

# ─── INIT CLIENT ───
keypair = load_keypair(SUI_KEY_STRING)
cfg = SuiConfig.user_config(prv_keys=[SUI_KEY_STRING], rpc_url=RPC_URL)
client = SyncClient(cfg)
SENDER = str(cfg.active_address)

# ─── DISCORD SETUP ───
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
balance_cache = {}

# ─── UTILS ───
def get_balance(address: str) -> int:
    try:
        result = client.get_balance(address=address)
        return int(result.total_balance)
    except Exception as e:
        logging.error("RPC lỗi khi lấy số dư: %s", e)
        return -1

def withdraw_all():
    try:
        gas_objects = client.gas_objects_owned_by_address(SENDER)
        if not gas_objects:
            logging.warning("⚠️ Không tìm thấy gas coin")
            return None

        gas_id = gas_objects[0].id
        txb = client.transfer_sui(
            signer=keypair,
            recipient=TARGET_ADDRESS,
            gas=gas_id,
            amount=None  # None = transfer toàn bộ trừ gas
        )
        if txb and txb.status == "success":
            return txb.digest
        else:
            err = txb.error if txb else "Không rõ lỗi"
            logging.error("❌ Tx thất bại: %s", err)
            return None
    except Exception as e:
        logging.error("Withdraw thất bại: %s", e)
        return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as e:
        logging.warning("Lỗi gửi Discord: %s", e)

# ─── TRACKER ───
@tasks.loop(seconds=POLL_INTERVAL)
async def tracker():
    addr = SENDER.lower()
    cur = get_balance(addr)
    if cur < 0:
        return

    prev = balance_cache.get(addr, 0)
    if cur != prev:
        await discord_send(f"💼 Số dư thay đổi: {prev/1e9:.4f} → {cur/1e9:.4f} SUI")

        if cur > 0:
            tx = withdraw_all()
            if tx:
                await discord_send(f"💸 Đã rút toàn bộ về `{TARGET_ADDRESS[:10]}...` · Tx `{tx}`")

    balance_cache[addr] = cur

# ─── BOT EVENTS ───
@bot.event
async def on_ready():
    await discord_send(f"🟢 Bot đã sẵn sàng - Đang theo dõi ví `{SENDER}`")
    tracker.start()
    bot.loop.create_task(start_web())

@bot.command()
async def ping(ctx):
    await ctx.send("✅ Pong")

@bot.command()
async def balance(ctx):
    bal = get_balance(SENDER)
    await ctx.send(f"Số dư: {bal/1e9:.4f} SUI" if bal >= 0 else "❌ RPC lỗi")

# ─── HTTP SERVER ───
async def handle(_):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

# ─── MAIN ───
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
    bot.run(DISCORD_TOKEN)