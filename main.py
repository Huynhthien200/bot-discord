"""
Discord SUI Balance Tracker Bot (Việt hoá)
=========================================

• Theo dõi biến động số dư của nhiều ví Sui.
• Cảnh báo lên Discord khi số dư thay đổi.
• Tuỳ chọn tự rút *toàn bộ* SUI về ví đích nếu phát hiện tiền vào ví nguồn.
• Được tối ưu để chạy trên Replit/Cyclic (HTTP health‑check, không block event‑loop).

Cấu trúc file:
--------------
main.py        – mã nguồn chính

Cách sử dụng:
-------------
1. Tạo tệp `.env` hoặc khai báo biến môi trường:
   DISCORD_TOKEN, DISCORD_CHANNEL_ID, SUI_PRIVATE_KEY, SUI_TARGET_ADDRESS
2. Cài đặt thư viện:
   pip install discord.py aiohttp httpx pysui
3. Chạy bot: `python main.py`

Lệnh Discord:
-------------
!ping                – kiểm tra bot còn sống
!balance             – xem số dư các ví đang theo dõi
!watch <name> <addr> – thêm ví mới
!unwatch <name>      – xoá ví khỏi danh sách

"""
from __future__ import annotations

import os
import asyncio
import concurrent.futures
from typing import Dict, Optional

import httpx
from aiohttp import web
import types, sys
sys.modules['audioop'] = types.ModuleType('audioop')   # stub cho Python 3.13
import discord

from discord.ext import commands, tasks

from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

# ─────────────────────────────── Cấu hình ────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_B64     = os.getenv("SUI_PRIVATE_KEY")     # chuỗi base64 của private key
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")  # ví nhận tự động rút

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_B64, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu DISCORD_TOKEN, DISCORD_CHANNEL_ID, SUI_PRIVATE_KEY hoặc SUI_TARGET_ADDRESS")

# ──────────────────────── Khởi tạo Sui client / key ───────────────────────
keypair = SuiKeyPair.from_b64(SUI_KEY_B64)
cfg     = SuiConfig.default_config()
client  = SyncClient(cfg)  # client đồng bộ (sẽ chạy trong thread riêng)

# ───────────────────────── Khởi tạo Discord bot ──────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# bộ nhớ cache số dư {địa_chỉ: số_dư}
balance_cache: Dict[str, int] = {}

# danh sách ví theo dõi {tên: địa_chỉ}
watched_accounts: Dict[str, str] = {
    "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
    "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
    "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
}

# HTTP client dùng lại 1 kết nối
http_client = httpx.AsyncClient(timeout=10.0)

# ThreadPoolExecutor cho các tác vụ đồng bộ nặng
executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)

# ────────────────────────── Hàm tiện ích ────────────────────────────────
async def get_balance(addr: str) -> Optional[int]:
    """Gọi RPC lấy tổng số dư SUI của địa chỉ."""
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "suix_getBalance",
        "params": [addr]
    }
    try:
        r = await http_client.post(cfg.rpc_url, json=payload)
        data = r.json()
        return int(data["result"]["totalBalance"])
    except Exception:
        return None

def _send_all_sui_sync() -> Optional[str]:
    """Chạy trong thread: gộp coin + chuyển toàn bộ SUI."""
    try:
        tx = SuiTransaction(client, initial_sender=keypair)
        tx.merge_coins()                       # gộp toàn bộ coin SUI
        tx.transfer_sui(recipient=TARGET_ADDRESS)  # chuyển hết
        res = tx.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as e:
        print("Send SUI error:", e)
    return None

async def send_all_sui() -> Optional[str]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(executor, _send_all_sui_sync)

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as e:
        print("Discord send error:", e)

# ─────────────────────────── Nhiệm vụ tracker ───────────────────────────
@tasks.loop(seconds=30)
async def tracker():
    for name, addr in watched_accounts.items():
        cur = await get_balance(addr)
        if cur is None:
            continue
        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta = (cur - prev) / 1e9  # chuyển về SUI (nano)
            arrow = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            await discord_send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI"
            )
            # Tự động rút nếu tiền vào ví nguồn (ví của keypair)
            if delta > 0 and addr.lower() == keypair.public_key.as_sui_address.lower():
                tx = await send_all_sui()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về `{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`"
                    )
        balance_cache[addr] = cur

# ──────────────────────── Sự kiện & Lệnh Discord ────────────────────────
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    print("🤖 Đã đăng nhập dưới tên", bot.user)

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

@bot.command()
async def watch(ctx, name: str, addr: str):
    watched_accounts[name] = addr.lower()
    await ctx.send(f"👀 Đã thêm ví **{name}**")

@bot.command()
async def unwatch(ctx, name: str):
    if watched_accounts.pop(name, None):
        await ctx.send(f"🚫 Đã xoá ví **{name}**")
    else:
        await ctx.send("⚠️ Không tìm thấy ví")

# ───────────────────────────── Webserver KA ──────────────────────────────
async def handle_ping(req):
    return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

# ────────────────────────────── Shutdown ─────────────────────────────────
async def shutdown():
    await http_client.aclose()
    executor.shutdown(wait=False)

# ─────────────────────────────── Main ────────────────────────────────────
if __name__ == "__main__":
    try:
        bot.run(DISCORD_TOKEN)
    finally:
        asyncio.run(shutdown())
