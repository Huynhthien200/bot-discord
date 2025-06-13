# main.py
import os
import asyncio
import gc
import psutil

import httpx
from aiohttp import web
import discord
from discord.ext import commands, tasks

from pysui import SyncClient, SuiConfig                # :contentReference[oaicite:0]{index=0}
from pysui.sui.sui_crypto import SuiKeyPair            # :contentReference[oaicite:1]{index=1}
from pysui.sui.sui_txn.sync_transaction import SuiTransaction  # :contentReference[oaicite:2]{index=2}

# ─── Environment ───────────────────────────────────────────────────────────
DISCORD_TOKEN      = os.getenv("DISCORD_TOKEN")
CHANNEL_ID         = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_B64        = os.getenv("SUI_PRIVATE_KEY")  # base64-encoded schema+key
TARGET_ADDRESS     = os.getenv("SUI_TARGET_ADDRESS")

assert DISCORD_TOKEN and CHANNEL_ID and SUI_KEY_B64 and TARGET_ADDRESS, "Missing one of DISCORD_TOKEN, DISCORD_CHANNEL_ID, SUI_PRIVATE_KEY, SUI_TARGET_ADDRESS"

# ─── Build SUI client & keypair ─────────────────────────────────────────────
# expects SUI_PRIVATE_KEY as the base64 keystring you get from SuiKeyPair.serialize()
keypair = SuiKeyPair.from_b64(SUI_KEY_B64)
cfg     = SuiConfig.default_config()
client  = SyncClient(cfg)  # synchronous JSON-RPC client :contentReference[oaicite:3]{index=3}

# ─── Discord bot setup ──────────────────────────────────────────────────────
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}

http_client = httpx.AsyncClient(timeout=10.0)

async def get_balance(addr: str) -> int | None:
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

def send_all_sui() -> str | None:
    try:
        # build a synchronous transfer transaction
        txer = SuiTransaction(client, initial_sender=keypair)  # use your keypair as sender
        txer.transfer_sui(recipient=TARGET_ADDRESS)           # amount omitted = full balance
        res = txer.execute()
        if res.effects.status.status == "success":
            return res.tx_digest
    except Exception as e:
        print("Send SUI error:", e)
    return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(CHANNEL_ID)
        await ch.send(msg)
    except Exception as e:
        print("Discord send error:", e)

@tasks.loop(seconds=10)  # polls every 10s
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
            # auto-withdraw if it's the monitored “source” account
            if delta > 0 and addr.lower() == keypair.public_key.as_sui_address.lower():
                tx = send_all_sui()
                if tx:
                    await discord_send(f"💸 **Đã rút toàn bộ SUI** về `{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`")
        balance_cache[addr] = cur

@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    print("🤖 Logged in as", bot.user)

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

# ─── keep-alive HTTP server ─────────────────────────────────────────────────
async def handle_ping(req):
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
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
