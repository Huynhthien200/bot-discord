import os
import json
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# --- ENV ---
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
INTERVAL = int(os.getenv("CHECK_INTERVAL", "1"))  # giây

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường cần thiết!")

if CHANNEL_ID == 0:
    raise ValueError("❌ Biến môi trường DISCORD_CHANNEL_ID chưa được cấu hình hoặc sai!")

# --- Ví theo dõi ---
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Đã tải {len(WATCHED)} ví từ watched.json")
except Exception as e:
    logging.error(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# --- SUI ---
try:
    cfg = SuiConfig.user_config(
        prv_keys=[SUI_PRIVATE_KEY],
        rpc_url=RPC_URL
    )
    client = SyncClient(cfg)
    withdraw_signer = str(cfg.active_address)
    logging.info(f"Kết nối SUI thành công! Địa chỉ ví: {withdraw_signer[:10]}...")
except Exception as e:
    logging.critical(f"Lỗi kết nối SUI: {e}")
    raise

# --- Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}  # addr -> balance

def safe_address(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    try:
        res = client.get_gas(address=addr)
        if not hasattr(res, "data") or not res.data:
            return 0.0
        return sum(int(obj.balance) for obj in res.data) / 1_000_000_000
    except Exception as e:
        logging.error(f"Lỗi khi kiểm tra số dư {safe_address(addr)}: {e}")
        return 0.0

async def withdraw_sui(from_addr: str, value: float) -> str | None:
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None
    try:
        gas_objs = client.get_gas(address=from_addr)
        if not hasattr(gas_objs, "data") or not gas_objs.data:
            logging.warning(f"⚠️ Không tìm thấy Gas Object cho {safe_address(from_addr)}")
            return None
        amount = int((value - 0.001) * 1_000_000_000)
        if amount <= 0:
            return None
        result = client.transfer(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=amount,
            gas_object=gas_objs.data[0].object_id
        )
        if hasattr(result, "tx_digest"):
            return result.tx_digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
    return None

@tasks.loop(seconds=INTERVAL)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = get_sui_balance(addr)
            prev = last_balances.get(addr, -1)
            if balance != prev and prev != -1:
                emoji = "🔼" if balance > prev else "🔽"
                await send_discord(
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Số dư: `{balance:.6f} SUI` ({'+' if balance-prev>0 else ''}{balance-prev:.6f})"
                )
            last_balances[addr] = balance

            if wallet.get("withdraw", False) and balance > 0.01:
                tx = await withdraw_sui(addr, balance)
                if tx:
                    await send_discord(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {wallet.get('name', safe_address(addr))}\n"
                        f"Số tiền: `{balance:.6f} SUI`\n"
                        f"TX: `{tx}`"
                    )
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

async def send_discord(msg: str):
    channel = bot.get_channel(CHANNEL_ID)
    if channel is None:
        logging.error("❌ Không tìm thấy kênh hoặc chưa cấp quyền cho bot!")
        for guild in bot.guilds:
            logging.info(f"Bot đang trong server: {guild.name}")
            for c in guild.text_channels:
                logging.info(f" - {c.name} ({c.id})")
        return
    try:
        await channel.send(msg)
    except Exception as e:
        logging.error(f"❌ Lỗi khi gửi tin nhắn Discord: {e}")

# --- Web server cho Railway ---
async def health_check(request):
    return web.Response(text=f"🟢 Bot đang chạy | Theo dõi {len(WATCHED)} ví")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

@bot.event
async def on_ready():
    await asyncio.sleep(2)  # delay để chắc chắn Discord đã sẵn sàng
    logging.info(f"Bot Discord đã sẵn sàng: {bot.user.name}")
    await send_discord(
        f"🚀 **Bot SUI Monitor đã khởi động**\n"
        f"• Theo dõi {len(WATCHED)} ví ({INTERVAL}s/kiểm tra)\n"
        f"• RPC: `{RPC_URL}`\n"
        f"• Ví chủ: `{safe_address(withdraw_signer)}`"
    )
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)