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
RPCS    = ["https://rpc-mainnet.suiscan.xyz/",
           "https://sui-mainnet-endpoint.blockvision.org"]
RPC_IDX = 0

# ─── Bech32 helpers ───────────────────────────────────────────────────────────
_BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"
def _polymod(v):
    g = (0x3b6a57b2,0x26508e6d,0x1ea119fa,0x3d4233dd,0x2a1462b3)
    chk = 1
    for x in v:
        b = chk >> 25
        chk = ((chk & 0x1ffffff) << 5) ^ x
        for i in range(5):
            chk ^= g[i] if ((b >> i) & 1) else 0
    return chk
def _hrp_expand(s): return [ord(c) >> 5 for c in s] + [0] + [ord(c) & 31 for c in s]
def _decode_bech32(b):
    b = b.lower(); p = b.rfind("1")
    hrp, data = b[:p], b[p+1:]
    data_v = [_BECH32_ALPHABET.find(c) for c in data]
    if min(data_v) == -1 or _polymod(_hrp_expand(hrp)+data_v) != 1:
        raise ValueError("Bech32 decode fail")
    return hrp, data_v[:-6]
def _convert_bits(data, f, t, pad=True):
    acc = bits = 0; out=[]; maxv=(1<<t)-1
    for v in data:
        acc = (acc<<f)|v; bits += f
        while bits >= t:
            bits -= t; out.append((acc>>bits)&maxv)
    if pad and bits: out.append((acc<<(t-bits))&maxv)
    return bytes(out)
def suiprivkey_to_b64(b):
    hrp, d = _decode_bech32(b)
    if hrp!="suiprivkey": raise ValueError("Sai định dạng")
    raw = _convert_bits(d,5,8,False)
    return base64.b64encode(raw).decode()

# ─── Keypair ───────────────────────────────────────────────────────────────────
def load_keypair(raw:str)->SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"): raw = suiprivkey_to_b64(raw)
    return SuiKeyPair.from_b64(raw)
keypair = load_keypair(SUI_KEY_STRING)

# ─── Client ────────────────────────────────────────────────────────────────────
cfg    = SuiConfig.user_config(rpc_url=RPCS[RPC_IDX], prv_keys=[SUI_KEY_STRING])
client = SyncClient(cfg)
SENDER_ADDR = str(cfg.active_address).lower()

# ─── Discord ───────────────────────────────────────────────────────────────────
intents = discord.Intents.default(); intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str,int] = {}
http_client = httpx.AsyncClient(timeout=10.0)

# ─── utils ─────────────────────────────────────────────────────────────────────
async def get_balance(addr:str)->int|None:
    p={"jsonrpc":"2.0","id":1,"method":"suix_getBalance","params":[addr]}
    try:
        r=await http_client.post(RPCS[RPC_IDX],json=p); r.raise_for_status()
        return int(r.json()["result"]["totalBalance"])
    except Exception as e:
        logging.warning("Lỗi RPC get_balance: %s",e); return None

def pay_all_sui()->str|None:
    try:
        tx = client.new_transaction()
        tx.transfer_sui(recipient=TARGET_ADDRESS)   # amount=None = toàn bộ
        res = tx.execute()
        if res.effects.status.status=="success":
            return res.tx_digest
    except Exception as e:
        logging.error("pay_all_sui thất bại: %s", e)
    return None

async def discord_send(msg:str):
    try:
        ch=await bot.fetch_channel(CHANNEL_ID); await ch.send(msg,silent=True)
    except Exception as e:
        logging.warning("Không gửi Discord: %s",e)

# ─── tracker ───────────────────────────────────────────────────────────────────
@tasks.loop(seconds=1)
async def tracker():
    for name, addr in WATCHED.items():
        cur=await get_balance(addr)
        if cur is None: continue
        prev=balance_cache.get(addr)
        if prev is not None and cur!=prev:
            delta=(cur-prev)/1e9
            arrow="🟢 TĂNG" if delta>0 else "🔴 GIẢM"
            await discord_send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI")
            if delta>0 and addr.lower()==SENDER_ADDR:
                tx=pay_all_sui()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về "
                        f"`{TARGET_ADDRESS[:6]}…`\n🔗 Tx: `{tx}`")
        balance_cache[addr]=cur

# ─── discord events ────────────────────────────────────────────────────────────
@bot.event
async def on_ready():
    bot.loop.create_task(start_webserver()); tracker.start()
    logging.info("🤖 Logged in as %s", bot.user)

@bot.command()
async def ping(ctx): await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):
    lines=[]
    for n,a in WATCHED.items():
        b=await get_balance(a)
        if b is not None: lines.append(f"💰 {n}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

# ─── keep-alive ────────────────────────────────────────────────────────────────
async def handle_ping(_): return web.Response(text="✅ Discord SUI bot is alive!")

async def start_webserver():
    app=web.Application(); app.router.add_get("/",handle_ping)
    runner=web.AppRunner(app); await runner.setup()
    await web.TCPSite(runner,"0.0.0.0",int(os.getenv("PORT","8080"))).start()

# ─── entry ─────────────────────────────────────────────────────────────────────
if __name__=="__main__":
    WATCHED={
        "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
        "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
        "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
    }
    bot.run(DISCORD_TOKEN)
