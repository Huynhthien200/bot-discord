#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import logging
import httpx
import types, sys

# ── stub audioop cho Python ≥ 3.13 ─────────────────────────────────
sys.modules["audioop"] = types.ModuleType("audioop")

from aiohttp import web
import discord
from discord.ext import commands, tasks

from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

# ── logging ───────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s")

# ── biến môi trường ───────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING  = os.getenv("SUI_PRIVATE_KEY")          # base64 hoặc suiprivkey…
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")
RPC_ENV         = os.getenv("SUI_RPC_LIST", "")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError(
        "Thiếu DISCORD_TOKEN, DISCORD_CHANNEL_ID, "
        "SUI_PRIVATE_KEY hoặc SUI_TARGET_ADDRESS"
    )

# ── khởi tạo RPC list ─────────────────────────────────────────────
rpc_list: list[str] = [r.strip() for r in RPC_ENV.split(",") if r.strip()] or [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
rpc_index: int = 0
http_client = httpx.AsyncClient(timeout=10.0)

# ── keypair ───────────────────────────────────────────────────────
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    try:
        if raw.startswith("suiprivkey") and hasattr(SuiKeyPair, "from_bech32"):
            return SuiKeyPair.from_bech32(raw)
        if hasattr(SuiKeyPair, "from_any"):
            return SuiKeyPair.from_any(raw)
        return SuiKeyPair.from_b64(raw)
    except Exception as exc:
        raise RuntimeError("Không decode được khóa Sui – kiểm tra SUI_PRIVATE_KEY!") from exc

keypair = load_keypair(SUI_KEY_STRING)

# ── SyncClient cho giao dịch (dùng cấu hình mặc định) ─────────────
client = SyncClient(SuiConfig.default_config())

# ── Discord bot ───────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}

# ── tiện ích RPC ──────────────────────────────────────────────────
def rotate_rpc() -> None:
    """Chọn RPC khác cho các lời gọi đọc số dư."""
    global rpc_index
    rpc_index = (rpc_index + 1) % len(rpc_list)

async def get_balance(addr: str) -> int | None:
    """Lấy số dư SUI (lamport) với cơ chế xoay RPC."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": "suix_getBalance", "params": [addr]}
    for _ in range(len(rpc_list)):
        try:
            r = await http_client.post(rpc_list[rpc_index], json=payload)
            r.raise_for_status()
            return int(r.json()["result"]["totalBalance"])
        except Exception as e:
            logging.warning("RPC %s lỗi: %s", rpc_list[rpc_index], e)
            rotate_rpc()
    return None

def send_all_sui() -> str | None:
    """Chuyển toàn bộ SUI về TARGET_ADDRESS, trả về digest nếu thành công."""
    try:
        tx = SuiTransaction(client, initial_sender=keypair)
        tx.transfer_sui(recipient=TARGET_ADDRESS)      # amount=None → toàn bộ
        res = tx.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as e:
        logging.error("Gửi SUI thất bại: %s", e)
    return None

# ── helper Discord ────────────────────────────────────────────────
async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as e:
        logging.warning("Không gửi được Discord: %s", e)

# ── vòng theo dõi số dư ───────────────────────────────────────────
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

# ── Discord events & commands ─────────────────────────────────────
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logging.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx):                                   # !ping
    await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):                                # !balance
    lines = []
    for name, addr in watched_accounts.items():
        b = await get_balance(addr)
        if b is not None:
            lines.append(f"💰 {name}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

# ── dịch vụ keep-alive ────────────────────────────────────────────
async def handle_ping(_):
    return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

# ── entry ─────────────────────────────────────────────────────────
if __name__ == "__main__":
    watched_accounts = {
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
