#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Discord bot: liên tục kiểm tra số dư Sui và rút sạch về TARGET_ADDRESS ngay
khi ví nguồn có SUI. Hỗ trợ khoá private-key dạng Base64 & Bech32.
(pysui 0.85 – Python ≥ 3.13)
"""

# ────────────── IMPORTS ───────────────────────────────────────────
import os, sys, types, base64, logging, httpx
from aiohttp import web

# stub audioop cho discord.py (Python 3.13+)
sys.modules["audioop"] = types.ModuleType("audioop")

from bech32 import bech32_decode, convertbits          # pip install bech32
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction
import discord
from discord.ext import commands, tasks

# ────────────── LOGGING ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s | %(levelname)-8s | %(message)s"
)

# ────────────── ENV ───────────────────────────────────────────────
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING  = os.getenv("SUI_PRIVATE_KEY")        # base64 hoặc suiprivkey…
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu biến môi trường cấu hình bot!")

# ────────────── RPC LIST ──────────────────────────────────────────
RPCS     = ["https://rpc-mainnet.suiscan.xyz/",
            "https://sui-mainnet-endpoint.blockvision.org"]
RPC_IDX  = 0

# ────────────── KEYPAIR ───────────────────────────────────────────
def _bech32_to_b64(bech: str) -> str:
    hrp, data = bech32_decode(bech)
    if hrp != "suiprivkey" or data is None:
        raise ValueError("Bech32 decode failed")
    raw = bytes(convertbits(data, 5, 8, False))
    if len(raw) != 64:
        raise ValueError("Invalid key length")
    return base64.b64encode(raw).decode()

def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        raw = _bech32_to_b64(raw)
    return SuiKeyPair.from_b64(raw)

keypair = load_keypair(SUI_KEY_STRING)

# ────────────── SUI CLIENT ────────────────────────────────────────
cfg    = SuiConfig.user_config(rpc_url=RPCS[RPC_IDX], prv_keys=[SUI_KEY_STRING])
client = SyncClient(cfg)
SENDER_ADDR = str(cfg.active_address).lower()

# ────────────── DISCORD BOT ───────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}
http_client = httpx.AsyncClient(timeout=10.0)

# ────────────── SUI HELPERS ───────────────────────────────────────
async def get_balance(addr: str) -> int | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "suix_getBalance", "params": [addr]}
    try:
        r = await http_client.post(RPCS[RPC_IDX], json=payload)
        r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as exc:
        logging.warning("RPC lỗi get_balance: %s", exc)
        return None

def sweep_all_sui() -> str | None:
    """Gửi toàn bộ SUI trong ví nguồn về TARGET_ADDRESS."""
    try:
        tx = SuiTransaction(client)
        tx.transfer_sui(recipient=TARGET_ADDRESS)      # amount=None → full balance
        res = tx.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as exc:
        logging.error("sweep_all_sui thất bại: %s", exc)
    return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as exc:
        logging.warning("Không gửi được Discord: %s", exc)

# ────────────── TRACKER (mỗi 1 giây) ─────────────────────────────
@tasks.loop(seconds=1.0)
async def tracker():
    for name, addr in WATCHED.items():
        cur = await get_balance(addr)
        if cur is None:
            continue

        prev = balance_cache.get(addr, 0)

        # ─── VÍ NGUỒN: nếu >0 thì rút sạch ngay ───
        if addr.lower() == SENDER_ADDR and cur > 0:
            tx = sweep_all_sui()
            if tx:
                await discord_send(
                    f"💸 **Phát hiện {cur/1e9:.4f} SUI – đã rút sạch** về "
                    f"`{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`"
                )
                balance_cache[addr] = 0      # giả định rút xong = 0
                continue                     # sang ví kế

        # ─── Ví khác: chỉ thông báo khi thay đổi ───
        if cur != prev:
            delta = (cur - prev) / 1e9
            arrow = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            await discord_send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI"
            )
        balance_cache[addr] = cur

# ────────────── DISCORD COMMANDS ──────────────────────────────────
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logging.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx):  # !ping
    await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):  # !balance
    lines = []
    for name, addr in WATCHED.items():
        b = await get_balance(addr)
        if b is not None:
            lines.append(f"💰 {name}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

# ────────────── HTTP KEEP-ALIVE ───────────────────────────────────
async def handle_ping(_):
    return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

# ────────────── MAIN ──────────────────────────────────────────────
if __name__ == "__main__":
    WATCHED = {
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
        # Thêm ví nguồn (keypair) vào danh sách để cũng hiển thị biến động
        "Ví nguồn":     SENDER_ADDR,
    }
    bot.run(DISCORD_TOKEN)
