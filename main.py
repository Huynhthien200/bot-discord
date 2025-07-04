import os
import json
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient

# === Cấu hình logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# === Biến môi trường ===
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường cần thiết!")

# === Đọc danh sách ví ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Đã tải {len(WATCHED)} ví từ watched.json")
except Exception as e:
    logging.error(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# === Kết nối SUI ===
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

# === Discord Bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}

def safe_address(addr: str) -> str:
    """Ẩn một phần địa chỉ ví để bảo mật"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

async def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI với cơ chế retry"""
    max_retries = 3
    for attempt in range(max_retries):
        try:
            coins = client.get_gas(address=addr)
            if coins and hasattr(coins, 'data'):
                total = sum(int(c.balance) for c in coins.data)
                return total / 1_000_000_000  # Convert từ MIST sang SUI
            return 0
        except Exception as e:
            if attempt == max_retries - 1:
                logging.error(f"Lỗi khi kiểm tra số dư {safe_address(addr)}: {e}")
                raise
            await asyncio.sleep(2)
            logging.warning(f"Thử lại lần {attempt + 1}...")
    return 0

async def withdraw_sui(from_addr: str) -> str | None:
    """Rút toàn bộ SUI về ví mục tiêu"""
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None

    try:
        # Lấy số dư chính xác
        coins = client.get_gas(address=from_addr)
        if not coins.data:
            logging.warning(f"⚠️ Không tìm thấy coins cho {safe_address(from_addr)}")
            return None
            
        balance = sum(int(c.balance) for c in coins.data) / 1_000_000_000
        if balance <= 0.001:  # Bỏ qua nếu số dư quá nhỏ
            logging.info(f"Số dư {balance} SUI quá nhỏ, bỏ qua")
            return None

        # Chọn gas object đầu tiên
        gas_obj = coins.data[0].object_id
        
        # Thực hiện giao dịch
        tx_result = client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=int(balance * 1_000_000_000),
            gas_object=gas_obj
        )

        if tx_result.tx_digest:
            logging.info(f"✅ Đã gửi {balance:.6f} SUI từ {safe_address(from_addr)}")
            
            # Gửi thông báo đến Discord
            try:
                channel = bot.get_channel(CHANNEL_ID)
                await channel.send(
                    f"💸 **Giao dịch thành công**\n"
                    f"• Từ: `{safe_address(from_addr)}`\n"
                    f"• Đến: `{safe_address(TARGET_ADDRESS)}`\n"
                    f"• Số lượng: `{balance:.6f} SUI`\n"
                    f"• TX Hash: `{tx_result.tx_digest}`"
                )
            except Exception as e:
                logging.error(f"Lỗi khi gửi thông báo Discord: {e}")
            
            return tx_result.tx_digest
            
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
        try:
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send(f"❌ Giao dịch thất bại từ `{safe_address(from_addr)}`: {str(e)}")
        except Exception as e:
            logging.error(f"Lỗi khi gửi thông báo lỗi: {e}")
    
    return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = await get_sui_balance(addr)
            last_balance = last_balances.get(addr, -1)

            # Thông báo thay đổi số dư
            if balance != last_balance and last_balance != -1:
                change = balance - last_balance
                emoji = "🔼" if change > 0 else "🔽"
                message = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Số dư: `{balance:.6f} SUI` ({'↑' if change > 0 else '↓'} {abs(change):.6f})"
                )
                try:
                    await bot.get_channel(CHANNEL_ID).send(message)
                except Exception as e:
                    logging.error(f"Lỗi gửi thông báo số dư: {e}")

            last_balances[addr] = balance

            # Tự động rút nếu được bật
            if wallet.get("withdraw", False) and balance > 0.001:
                await withdraw_sui(addr)
                
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

# === Web Server for Railway ===
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
    logging.info(f"Bot Discord đã sẵn sàng: {bot.user.name}")
    try:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send(
            f"🚀 **Bot SUI Monitor đã khởi động**\n"
            f"• Theo dõi {len(WATCHED)} ví (5s/kiểm tra)\n"
            f"• RPC: `{RPC_URL}`\n"
            f"• Ví chủ: `{safe_address(withdraw_signer)}`\n"
            f"• Ví đích: `{safe_address(TARGET_ADDRESS)}`"
        )
    except Exception as e:
        logging.error(f"Lỗi gửi tin nhắn khởi động: {e}")
    
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
