#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import os, sys, types, base64, logging, httpx
from aiohttp import web

# ─── stub audioop (Python ≥3.13) ───────────────────────────────────────────────
sys.modules["audioop"] = types.ModuleType("audioop")

import discord
from discord.ext import commands, tasks
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from pysui.sui.sui_txn.sync_transaction import SuiTransaction

# ─── log ───────────────────────────────────────────────────────────────────────
logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s | %(levelname)-8s | %(message)s")

# ─── ENV ───────────────────────────────────────────────────────────────────────
DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
CHANNEL_ID     = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_KEY_STRING = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_KEY_STRING, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu biến môi trường thiết yếu.")

# ─── RPC ───────────────────────────────────────────────────────────────────────
RPCS = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
RPC_IDX = 0

# ─── Bech32 helpers (tối giản) ────────────────────────────────────────────────
BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

def _bech32_polymod(values):
    GEN = (0x3b6a57b2, 0x26508e6d, 0x1ea119fa, 0x3d4233dd, 0x2a1462b3)
    chk = 1
    for v in values:
        b = (chk >> 25) & 0xFF
        chk = ((chk & 0x1FFFFFF) << 5) ^ v
        for i in range(5):
            chk ^= GEN[i] if ((b >> i) & 1) else 0
    return chk

def _bech32_hrp_expand(hrp):
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]

def _bech32_verify(hrp, data):
    return _bech32_polymod(_bech32_hrp_expand(hrp) + data) == 1

def _bech32_decode(bech):
    bech = bech.lower()
    pos  = bech.rfind('1')
    hrp, data = bech[:pos], bech[pos+1:]
    decoded = [BECH32_ALPHABET.find(c) for c in data]
    if min(decoded) == -1 or not _bech32_verify(hrp, decoded):
        raise ValueError("Bech32 decode fail")
    return hrp, decoded[:-6]          # strip checksum

def _convert_bits(data, from_bits, to_bits, pad=True):
    acc = 0
    bits = 0
    out  = []
    maxv = (1 << to_bits) - 1
    for value in data:
        acc = (acc << from_bits) | value
        bits += from_bits
        while bits >= to_bits:
            bits -= to_bits
            out.append((acc >> bits) & maxv)
    if pad and bits:
        out.append((acc << (to_bits - bits)) & maxv)
    return bytes(out)

def suiprivkey_to_b64(bech32_key: str) -> str:
    hrp, data = _bech32_decode(bech32_key)
    if hrp != "suiprivkey":
        raise ValueError("Không phải định dạng suiprivkey")
    raw = _convert_bits(data, 5, 8, False)
    return base64.b64encode(raw).decode()

# ─── Keypair ───────────────────────────────────────────────────────────────────
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        raw = suiprivkey_to_b64(raw)
    return SuiKeyPair.from_b64(raw)

keypair = load_keypair(SUI_KEY_STRING)

# ─── Client / Discord ─────────────────────────────────────────────────────────
cfg    = SuiConfig.user_config(rpc_url=RPCS[RPC_IDX], prv_keys=[SUI_KEY_STRING])
client = SyncClient(cfg)
SENDER_ADDR = str(cfg.active_address).lower()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}
http_client = httpx.AsyncClient(timeout=10.0)

async def get_balance(addr: str) -> int | None:
    payload = {"jsonrpc": "2.0", "id": 1, "method": "suix_getBalance", "params": [addr]}
    try:
        r = await http_client.post(RPCS[RPC_IDX], json=payload)
        r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as exc:
        logging.warning("Lỗi RPC get_balance: %s", exc)
        return None

def pay_all_sui() -> str | None:
    try:
        tx = SuiTransaction(client, initial_sender=keypair)
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
        await ch.send(msg, silent=True)
    except Exception as exc:
        logging.warning("Không gửi được Discord: %s", exc)

@tasks.loop(seconds=1)
async def tracker():
    for name, addr in WATCHED.items():
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
            if delta > 0 and addr.lower() == SENDER_ADDR:
                tx = pay_all_sui()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về "
                        f"`{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`"
                    )
        balance_cache[addr] = cur

@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver())
    tracker.start()
    logging.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx):
    await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):
    lines = [
        f"💰 {name}: {await get_balance(addr)/1e9:.4f} SUI"
        for name, addr in WATCHED.items()
    ]
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

async def handle_ping(_):
    return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app = web.Application()
    app.router.add_get("/", handle_ping)
    runner = web.AppRunner(app)
    await runner.setup()
    await web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080"))).start()

# ─── entry ─────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    WATCHED = {
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
