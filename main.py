import os
import json
import logging
import asyncio
import discord
import httpx
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient
from pysui.sui.sui_crypto import SuiKeyPair
from bech32 import bech32_decode, convertbits
import base64

# === Logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# === Env vars ===
RPC_URL         = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường!")

# === Load watched.json ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Đã tải {len(WATCHED)} ví từ watched.json")
except Exception as e:
    logging.error(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# === Helper: load SuiKeyPair từ Bech32 / Base64 ===
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        hrp, data = bech32_decode(raw)
        if hrp != "suiprivkey" or not data:
            raise RuntimeError("Key Bech32 không hợp lệ")
        key_bytes = bytes(convertbits(data, 5, 8, False))
        b64 = base64.b64encode(key_bytes).decode()
        return SuiKeyPair.from_b64(b64)
    else:
        return SuiKeyPair.from_b64(raw)

# === Init Sui client & keypair ===
cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
client = SyncClient(cfg)
keypair = load_keypair(SUI_PRIVATE_KEY)
withdraw_signer = str(cfg.active_address)
logging.info(f"SuiConfig active address: {withdraw_signer}")

# === HTTP client for JSON-RPC ===
http_client = httpx.AsyncClient(timeout=10)

async def get_sui_balance(addr: str) -> float:
    """Gọi suix_getBalance, trả về float SUI"""
    try:
        payload = {"jsonrpc":"2.0","id":1,"method":"suix_getBalance","params":[addr]}
        r = await http_client.post(RPC_URL, json=payload)
        r.raise_for_status()
        total = int(r.json()["result"]["totalBalance"])
        return total / 1e9
    except Exception as e:
        logging.error(f"Lỗi RPC lấy balance {addr[:8]}…: {e}")
        return 0.0

async def withdraw_sui(addr: str) -> str | None:
    """Rút toàn bộ SUI từ addr về TARGET_ADDRESS"""
    if addr != withdraw_signer:
        logging.warning(f"Không có quyền rút từ {addr}")
        return None

    bal = await get_sui_balance(addr)
    if bal <= 0:
        return None

    # Lấy gas-coin để dùng fee
    # JSON-RPC suix_getGasObjects không chuẩn, nên dùng pysui.get_gas():
    gas_res = client.get_gas(address=addr)  # dù deprecated vẫn còn
    if not gas_res.result_data.data:
        logging.warning("Không tìm thấy gas object")
        return None
    gas_obj = gas_res.result_data.data[0]

    try:
        tx = client.transfer_sui(
            signer=keypair,
            recipient=TARGET_ADDRESS,
            amount=int(bal * 1e9),
            gas_object=gas_obj.object_id
        )
        return tx.tx_digest
    except Exception as e:
        logging.error(f"Lỗi khi rút từ {addr[:8]}…: {e}")
        return None

# === Discord setup ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances: dict[str, float] = {}

def safe(addr: str) -> str:
    return f"{addr[:6]}…{addr[-4:]}"
    
@tasks.loop(seconds=5)
async def monitor():
    for w in WATCHED:
        addr = w["address"]
        name = w.get("name", safe(addr))
        bal  = await get_sui_balance(addr)
        prev = last_balances.get(addr, None)

        # Thông báo khi thay đổi
        if prev is not None and bal != prev:
            emoji = "🔼" if bal > prev else "🔽"
            await bot.get_channel(CHANNEL_ID).send(
                f"**{name}** ({safe(addr)})\n{emoji} `{bal:.6f} SUI` (trước: {prev:.6f})"
            )
        last_balances[addr] = bal

        # Nếu wallet có withdraw=true và balance>0 → rút
        if w.get("withdraw", False) and bal > 0:
            tx = await withdraw_sui(addr)
            if tx:
                await bot.get_channel(CHANNEL_ID).send(
                    f"💸 **Rút tự động**\nVí: {name}\nSố dư: `{bal:.6f} SUI`\nTx: `{tx}`"
                )

# === Keep-alive web for Railway ===
async def ping(_):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", ping)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT","8080")))
    await site.start()

@bot.event
async def on_ready():
    logging.info("Bot started. Watching %d wallets.", len(WATCHED))
    await bot.get_channel(CHANNEL_ID).send(f"🟢 Bot đã khởi động, theo dõi {len(WATCHED)} ví.")
    monitor.start()
    bot.loop.create_task(start_web())

bot.run(DISCORD_TOKEN)
