#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord bot: theo dõi ví Sui – báo tăng/giảm và rút toàn bộ SUI khi tiền vào.
Dùng pysui 0.85 (SyncClient + SuiTransaction.transfer_sui).
"""

import os
import sys
import types
import logging
import httpx
from aiohttp import web

# ───────── stub audioop cho Python 3.13+ ─────────
sys.modules["audioop"] = types.ModuleType("audioop")

import discord
from discord.ext import commands, tasks
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

# ───────── logging ─────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)

# ───────── biến môi trường ─────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING  = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError(
        "Thiếu DISCORD_TOKEN, DISCORD_CHANNEL_ID, SUI_PRIVATE_KEY hoặc SUI_TARGET_ADDRESS"
    )

# ───────── RPC ─────────
RPCS     = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
RPC_URL  = RPCS[0]

# ───────── keypair & client ─────────
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if hasattr(SuiKeyPair, "from_any"):
        return SuiKeyPair.from_any(raw)
    return SuiKeyPair.from_b64(raw)

keypair       = load_keypair(SUI_KEY_STRING)
cfg           = SuiConfig.user_config(rpc_url=RPC_URL, prv_keys=[SUI_KEY_STRING])
client        = SyncClient(cfg)
SENDER_ADDR   = str(cfg.active_address).lower()

# ───────── Discord ─────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}
http_client = httpx.AsyncClient(http2=True, timeout=10.0)

# ───────── util ─────────
async def get_balance(addr: str) -> int | None:
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "suix_getBalance",
        "params":  [addr],
    }
    try:
        r = await http_client.post(RPC_URL, json=payload)
        r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as exc:
        logging.warning("RPC get_balance lỗi: %s", exc)
        return None

def send_all_sui() -> str | None:
    """Chuyển toàn bộ SUI trong ví keypair về TARGET_ADDRESS."""
    try:
        tx = SuiTransaction(client, initial_sender=keypair)
        tx.transfer_sui(recipient=TARGET_ADDRESS)      # amount=None => gửi hết
        res = tx.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as exc:
        logging.error("send_all_sui thất bại: %s", exc)
    return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as exc:
        logging.warning("Không gửi Discord: %s", exc)

# ───────── vòng lặp theo dõi ─────────
@tasks.loop(seconds=1.0)
async def tracker():
    for name, addr in WATCHED.items():
        cur = await get_balance(addr)
        if cur is None:
            continue

        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta  = (cur - prev) / 1e9               # lamports → SUI
            arrow  = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            change = f"{abs(delta):.4f} SUI"

            await discord_send(
                f"🚨 **{name} thay đổi số dư!** {arrow} {change}\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI"
            )

            # tự động rút khi số dư tăng ở ví keypair
            if delta > 0 and addr.lower() == SENDER_ADDR:
                tx = send_all_sui()
                if tx:
                    await discord_send(
                        f"💸 Đã rút toàn bộ về `{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`"
                    )

        balance_cache[addr] = cur

# ───────── Discord commands ─────────
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logging.info("🤖 Đăng nhập Discord thành công: %s", bot.user)

@bot.command()
async def ping(ctx):
    await ctx.send("✅ Bot vẫn sống!")

@bot.command()
async def balance(ctx):
    lines = []
    for name, addr in WATCHED.items():
        bal = await get_balance(addr)
        if bal is not None:
            lines.append(f"💰 {name}: {bal/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) if lines else "⚠️ RPC lỗi")

# ───────── HTTP keep-alive ─────────
async def handle_ping(_):
    return web.Response(text="✅ Bot alive")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

# ───────── main ─────────
if __name__ == "__main__":
    WATCHED = {
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
