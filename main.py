#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord bot theo dõi số dư Sui – phiên bản hỗ trợ chuỗi khóa Base64 & Bech32
• Tự xoay vòng nhiều RPC endpoint (mặc định 2 endpoint, hoặc cấu hình qua biến SUI_RPC_LIST)
• Tự động chuyển tiền về ví đích khi phát hiện nạp vào ví nguồn
• Kèm web‑server đơn giản để Render health‑check
"""

from __future__ import annotations

import os
import logging
import json
from typing import List

import httpx
from aiohttp import web
import types, sys
sys.modules['audioop'] = types.ModuleType('audioop')   # stub cho Python 3.13
import discord
from discord.ext import commands, tasks

from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

###############################################################################
# Logging
###############################################################################
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
)
logger = logging.getLogger("sui-discord-bot")

###############################################################################
# Biến môi trường bắt buộc
###############################################################################
DISCORD_TOKEN   = os.environ.get("DISCORD_TOKEN")
CHANNEL_ID      = int(os.environ.get("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING  = os.environ.get("SUI_PRIVATE_KEY")      # base64 hoặc suiprivkey…
TARGET_ADDRESS  = os.environ.get("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu DISCORD_TOKEN, DISCORD_CHANNEL_ID, "
                       "SUI_PRIVATE_KEY hoặc SUI_TARGET_ADDRESS")

###############################################################################
# RPC list & helper
###############################################################################
# Nếu đặt SUI_RPC_LIST="https://rpc1,https://rpc2" sẽ ghi đè danh sách mặc định
rpc_list: List[str] = (
    [u.strip() for u in os.getenv("SUI_RPC_LIST", "").split(",") if u.strip()] or
    [
        "https://rpc-mainnet.suiscan.xyz/",
        "https://sui-mainnet-endpoint.blockvision.org",
    ]
)
rpc_index = 0  # vị trí hiện tại trong danh sách


def current_rpc() -> str:
    """Trả về RPC endpoint hiện tại."""
    return rpc_list[rpc_index]


def switch_rpc() -> None:
    """Xoay sang RPC tiếp theo trong danh sách."""
    global rpc_index
    rpc_index = (rpc_index + 1) % len(rpc_list)
    logger.warning("🏃 Đổi RPC sang %s", current_rpc())

###############################################################################
# Tải keypair Sui
###############################################################################

def load_keypair(keystr: str) -> SuiKeyPair:
    keystr = keystr.strip()
    try:
        if hasattr(SuiKeyPair, "from_any"):
            return SuiKeyPair.from_any(keystr)
        if keystr.startswith("suiprivkey"):
            return SuiKeyPair.from_string(keystr)
        return SuiKeyPair.from_b64(keystr)
    except Exception as exc:
        raise RuntimeError("Không decode được khóa Sui – kiểm tra SUI_PRIVATE_KEY!") from exc


auth_keypair = load_keypair(SUI_KEY_STRING)

###############################################################################
# Tạo SyncClient (sử dụng RPC hiện tại)
###############################################################################

def make_client() -> SyncClient:
    cfg = SuiConfig.custom_config(rpc_url=current_rpc()) if hasattr(SuiConfig, "custom_config") else SuiConfig.default_config()
    # Nếu pysui <0.90 chưa có custom_config thì sửa trực tiếp
    if not hasattr(SuiConfig, "custom_config"):
        cfg.rpc_url = current_rpc()
    return SyncClient(cfg)


client = make_client()

###############################################################################
# Thiết lập Discord bot
###############################################################################
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}
http_client = httpx.AsyncClient(timeout=10.0)

###############################################################################
# RPC helpers
###############################################################################
async def call_rpc(method: str, params: list) -> dict | None:
    """Gọi RPC; tự xoay vòng khi lỗi."""
    for _ in range(len(rpc_list)):
        try:
            resp = await http_client.post(
                current_rpc(),
                json={"jsonrpc": "2.0", "id": 1, "method": method, "params": params},
            )
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(data["error"])
            return data["result"]
        except Exception as err:
            logger.warning("RPC %s lỗi: %s", current_rpc(), err)
            switch_rpc()
    return None


async def get_balance(address: str) -> int | None:
    result = await call_rpc("suix_getBalance", [address])
    if result:
        return int(result["totalBalance"])
    return None

###############################################################################
# Gửi toàn bộ SUI về ví đích
###############################################################################

def send_all_sui() -> str | None:
    global client
    for _ in range(len(rpc_list)):
        try:
            txer = SuiTransaction(client, initial_sender=auth_keypair)
            txer.transfer_sui(recipient=TARGET_ADDRESS)  # amount=None => full balance
            res = txer.execute()
            if res.effects.status.status == "success":
                return res.tx_digest
        except Exception as exc:
            logger.error("Gửi SUI thất bại qua %s: %s", current_rpc(), exc)
            switch_rpc()
            client = make_client()  # tạo client mới với RPC mới
    return None

###############################################################################
# Discord helper
###############################################################################
async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as e:
        logger.warning("Không gửi được Discord: %s", e)

###############################################################################
# Theo dõi số dư
###############################################################################
watched_accounts = {
    "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
    "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
    "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    "Ví Nguồn":     auth_keypair.public_key.as_sui_address,
}


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
            # tự rút nếu vừa có tiền vào ví nguồn
            if delta > 0 and addr.lower() == auth_keypair.public_key.as_sui_address.lower():
                tx = send_all_sui()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về `{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`"
                    )
        balance_cache[addr] = cur

###############################################################################
# Discord events / commands
###############################################################################
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logger.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx):
    await ctx.send("🏓 Pong!")

@bot.command()
async def balance(ctx):
    lines = []
    for name, addr in watched_accounts.items():
        bal = await get_balance(addr)
        if bal is not None:
            lines.append(f"💰 {name}: {bal/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

###############################################################################
# Mini web‑server cho health‑check (Render)   
###############################################################################
async def handle_ping(_):
    return web.Response(text="✅ Bot Sui chạy OK")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

###############################################################################
# Main
###############################################################################
if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
