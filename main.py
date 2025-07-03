import os
import json
import logging
import asyncio
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient
from pysui.sui.sui_clients import SuiClient

# === Logging setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# === Env vars ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường!")

# === Load watched wallets ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
except Exception as e:
    logging.error(f"Lỗi đọc file watched.json: {e}")
    WATCHED = []

# === Sui setup ===
try:
    cfg = SuiConfig.user_config(
        prv_keys=[SUI_PRIVATE_KEY],
        rpc_url=RPC_URL
    )
    client = SuiClient(cfg)
    withdraw_signer = str(client.active_address)
    logging.info(f"Kết nối SUI thành công! Địa chỉ ví: {withdraw_signer}")
except Exception as e:
    logging.error(f"Lỗi cấu hình SUI: {e}")
    raise

# === Discord bot ===
intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}

def get_balance(addr: str) -> int:
    try:
        coins = client.get_all_coins(addr).result_data
        return sum(int(c.balance) for c in coins)
    except Exception as e:
        logging.error(f"Lỗi RPC khi kiểm tra số dư {addr}: {e}")
        return -1

def withdraw_all(from_addr: str) -> str | None:
    try:
        if from_addr != withdraw_signer:
            logging.warning(f"⚠️ Không thể rút từ ví {from_addr} (không khớp ví ký)")
            return None
            
        gas_objects = client.get_gas(from_addr).result_data
        if not gas_objects:
            logging.warning("⚠️ Không tìm thấy Gas Object")
            return None
            
        balance = get_balance(from_addr)
        if balance <= 0:
            return None
            
        tx_result = client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=balance,
            gas_budget=10_000_000
        )
        
        if tx_result.result_data:
            digest = tx_result.result_data.tx_digest
            logging.info(f"✅ Đã rút {balance/1e9} SUI từ {from_addr[:8]}... -> {TARGET_ADDRESS[:8]}... | TX: {digest}")
            return digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
    return None

@tasks.loop(seconds=1)
async def monitor():
    for wallet in WATCHED:
        addr = wallet["address"]
        is_withdraw = wallet.get("withdraw", False)

        balance = get_balance(addr)
        last = last_balances.get(addr, -1)

        if balance != last:
            logging.info(f"📊 {addr[:8]}... | Số dư thay đổi: {last/1e9} → {balance/1e9} SUI")
            last_balances[addr] = balance
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send(f"🔔 **{addr[:8]}...**\nSố dư: `{balance/1e9:.3f} SUI` ({'⬆️' if balance > last else '⬇️'})")

        if balance > 0 and is_withdraw:
            tx_hash = withdraw_all(addr)
            if tx_hash:
                channel = bot.get_channel(CHANNEL_ID)
                await channel.send(f"💸 **Đã rút** `{balance/1e9:.3f} SUI`\n→ {TARGET_ADDRESS[:8]}...\n📜 TX: `{tx_hash}`")

# === Web server for keep-alive ===
async def health_check(_):
    return web.Response(text="🟢 Bot đang hoạt động")

async def start_web():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", 8080)
    await site.start()

@bot.event
async def on_ready():
    logging.info(f"✅ Bot Discord đã sẵn sàng (User: {bot.user.name})")
    channel = bot.get_channel(CHANNEL_ID)
    await channel.send(f"🚀 **Bot đã khởi động**\nĐang theo dõi {len(WATCHED)} ví SUI")
    monitor.start()
    await start_web()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
