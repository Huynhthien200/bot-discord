#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, types, logging, base64, httpx
from aiohttp import web

# ───────── stub `audioop` cho Python ≥ 3.13 ─────────
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
SUI_KEY_STRING  = os.getenv("SUI_PRIVATE_KEY")          # base64 hoặc suiprivkey…
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu DISCORD_TOKEN, DISCORD_CHANNEL_ID, "
                       "SUI_PRIVATE_KEY hoặc SUI_TARGET_ADDRESS")

# ───────── RPC ─────────
RPCS    = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
RPC_IDX = 0

# ───────── chuyển suiprivkey → base64 (nếu cần) ─────────
def _bech32_to_b64(bech: str) -> str:
    try:
        from bech32 import bech32_decode, convertbits       # pip install bech32
        hrp, data = bech32_decode(bech)
        if hrp != "suiprivkey" or data is None:
            raise ValueError("Sai định dạng bech32")
        key_bytes = bytes(convertbits(data, 5, 8, False))
        return base64.b64encode(key_bytes).decode()
    except Exception as exc:
        raise RuntimeError("Không decode được khoá Bech32") from exc

# ───────── keypair ─────────
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        raw = _bech32_to_b64(raw)

    if hasattr(SuiKeyPair, "from_any"):
        return SuiKeyPair.from_any(raw)
    return SuiKeyPair.from_b64(raw)

keypair = load_keypair(SUI_KEY_STRING)

# ───────── client Sui ─────────
cfg    = SuiConfig.user_config(rpc_url=RPCS[RPC_IDX], prv_keys=[SUI_KEY_STRING])
client = SyncClient(cfg)
SENDER_ADDR = str(cfg.active_address).lower()

# ───────── Discord ─────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}
http_client = httpx.AsyncClient(timeout=10.0)

# ───────── tiện ích ─────────
async def get_balance(addr: str) -> int | None:
    payload = {
        "jsonrpc": "2.0",
        "id":      1,
        "method":  "suix_getBalance",
        "params":  [addr],
    }
    try:
        r = await http_client.post(RPCS[RPC_IDX], json=payload)
        r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as exc:
        logging.warning("Lỗi RPC get_balance: %s", exc)
        return None

def pay_all_sui() -> str | None:
    """
    Chuyển toàn bộ SUI trong ví keypair tới TARGET_ADDRESS.
    Trả về tx_digest nếu thành công, None nếu lỗi.
    """
    try:
        tx = SuiTransaction(client=client, initial_sender=keypair)
        tx.pay_all_sui(recipients=[TARGET_ADDRESS])
        res = tx.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as exc:
        logging.error("pay_all_sui thất bại: %s", exc)
    return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as exc:
        logging.warning("Không gửi được Discord: %s", exc)

# ───────── theo dõi ─────────
@tasks.loop(seconds=1)
async def tracker():
    for name, addr in WATCHED.items():
        cur = await get_balance(addr)
        if cur is None:
            continue

        if addr.lower() == SENDER_ADDR and cur > 0:            # ví nguồn có tiền ⇒ rút
            tx = pay_all_sui()
            if tx:
                await discord_send(
                    f"💸 **Đã rút {cur/1e9:.4f} SUI** về "
                    f"`{TARGET_ADDRESS[:6]}…`   🔗 `{tx}`"
                )
                balance_cache[addr] = 0
                continue

        prev = balance_cache.get(addr, cur)
        if cur != prev:
            delta = (cur - prev) / 1e9
            arrow = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            await discord_send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f}"
            )
        balance_cache[addr] = cur

# ───────── Discord events ─────────
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logging.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx):          # !ping
    await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):       # !balance
    lines = []
    for name, addr in WATCHED.items():
        b = await get_balance(addr)
        if b is not None:
            lines.append(f"💰 {name}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

# ───────── HTTP keep-alive ─────────
async def handle_ping(_):
    return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

# ───────── entry ─────────
if __name__ == "__main__":
    WATCHED = {
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
