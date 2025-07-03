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
from pysui.sui.sui_txn.sync_transaction import SuiTransaction
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

# === Helper: load SuiKeyPair from Bech32 or Base64 ===
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    if raw.startswith("suiprivkey"):
        hrp, data = bech32_decode(raw)
        if hrp != "suiprivkey" or not data:
            raise RuntimeError("Key Bech32 không hợp lệ")
        key_bytes = bytes(convertbits(data, 5, 8, False))
        b64 = base64.b64encode(key_bytes).decode()
        return SuiKeyPair.from_b64(b64)
    return SuiKeyPair.from_b64(raw)

# === Init Sui client & keypair ===
cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
client = SyncClient(cfg)
keypair = load_keypair(SUI_PRIVATE_KEY)
withdraw_signer = str(cfg.active_address)
logging.info(f"SuiConfig active address (rút): {withdraw_signer}")

# === HTTP client for JSON-RPC ===
http_client = httpx.AsyncClient(timeout=10)

async def get_sui_balance(addr: str) -> float:
    """Gọi suix_getBalance, trả về float SUI"""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "suix_getBalance",
            "params": [addr]
        }
        r = await http_client.post(RPC_URL, json=payload)
        r.raise_for_status()
        total = int(r.json()["result"]["totalBalance"])
        return total / 1e9
    except Exception as e:
        logging.error(f"Lỗi RPC lấy balance {addr[:8]}…: {e}")
        return 0.0

async def withdraw_sui(from_addr: str) -> str | None:
    """Rút toàn bộ SUI từ from_addr về TARGET_ADDRESS bằng SuiTransaction"""
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {from_addr}")
        return None

    bal = await get_sui_balance(from_addr)
    if bal <= 0:
        return None

    # Lấy gas object qua client.get_gas
    gas_res = await asyncio.to_thread(client.get_gas, address=from_addr)
    gas_list = gas_res.result_data.data
    if not gas_list:
        logging.warning(f"⚠️ Không tìm thấy gas object cho {from_addr}")
        return None

    def build_and_send():
        tx = SuiTransaction(client=client, initial_sender=from_addr)
        tx.transfer_sui(
            recipient=TARGET_ADDRESS,
            from_coin=gas_list[0].object_id,
            amount=int(bal * 1e9)
        )
        result = tx.execute()
        return result.tx_digest

    try:
        digest = await asyncio.to_thread(build_and_send)
        logging.info(f"💸 Đã rút {bal:.6f} SUI → {TARGET_ADDRESS[:10]}… · Tx: {digest}")
        return digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
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

        # Gửi thông báo khi số dư thay đổi
        if prev is not None and bal != prev:
            emoji = "🔼" if bal > prev else "🔽"
            await bot.get_channel(CHANNEL_ID).send(
                f"**{name}** ({safe(addr)})\n"
                f"{emoji} `{bal:.6f} SUI` (trước: {prev:.6f})"
            )
        last_balances[addr] = bal

        # Nếu withdraw=true thì tự động rút
        if w.get("withdraw", False) and bal > 0:
            tx = await withdraw_sui(addr)
            if tx:
                await bot.get_channel(CHANNEL_ID).send(
                    f"💸 **Đã rút tự động**\n"
                    f"Ví: {name}\n"
                    f"Số dư: `{bal:.6f} SUI`\n"
                    f"Tx: `{tx}`"
                )

@bot.command()
async def xemtokens(ctx, address: str):
    bal = await get_sui_balance(address)
    await ctx.send(f"Số dư của `{address}`: `{bal:.6f} SUI`")

# === Keep-alive server for Railway ===
async def health(request):
    return web.Response(text="OK")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT","8080")))
    await site.start()

@bot.event
async def on_ready():
    logging.info(f"Bot started. Monitoring {len(WATCHED)} wallets.")
    await bot.get_channel(CHANNEL_ID).send(f"🟢 Bot đã khởi động, theo dõi {len(WATCHED)} ví.")
    monitor.start()
    bot.loop.create_task(start_web())

bot.run(DISCORD_TOKEN)
