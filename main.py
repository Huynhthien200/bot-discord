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

last_balances = {}  # addr -> số dư SUI lần trước

def safe_address(addr: str) -> str:
    """Ẩn một phần địa chỉ ví để bảo mật"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI (SUI) bằng get_gas"""
    try:
        res = client.get_gas(address=addr)
        coins = getattr(res, "data", []) or []
        return sum(int(c.balance) / 1_000_000_000 for c in coins)
    except Exception as e:
        logging.error(f"Lỗi khi kiểm tra số dư {safe_address(addr)}: {e}")
        return -1

def withdraw_sui(from_addr: str) -> str | None:
    """Rút toàn bộ SUI về ví mục tiêu"""
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None

    try:
        balance = get_sui_balance(from_addr)
        if balance <= 0:
            return None

        gas_objs = client.get_gas(address=from_addr)
        coins = getattr(gas_objs, "data", []) or []
        if not coins:
            logging.warning(f"⚠️ Không tìm thấy Gas Object cho {safe_address(from_addr)}")
            return None

        amount = int(balance * 1_000_000_000)
        tx_result = client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=amount,
            gas_object=coins[0].object_id
        )
        return getattr(tx_result, 'tx_digest', None)
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
        return None

@tasks.loop(seconds=1)  # Kiểm tra mỗi 1 giây
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = get_sui_balance(addr)
            prev_balance = last_balances.get(addr, -1)
            # Thông báo nếu số dư thay đổi
            if prev_balance != -1 and abs(balance - prev_balance) > 0:
                change = balance - prev_balance
                emoji = "🔼" if change > 0 else "🔽"
                message = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Số dư: `{balance:.4f} SUI` ({'+' if change>0 else ''}{change:.4f})"
                )
                await bot.get_channel(CHANNEL_ID).send(message)
            last_balances[addr] = balance

            # Rút nếu ví được bật withdraw và có tiền
            if wallet.get("withdraw", False) and balance > 0:
                tx_hash = withdraw_sui(addr)
                if tx_hash:
                    await bot.get_channel(CHANNEL_ID).send(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {wallet.get('name', safe_address(addr))}\n"
                        f"Số tiền: `{balance:.4f} SUI`\n"
                        f"TX: `{tx_hash}`"
                    )
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
        await bot.get_channel(CHANNEL_ID).send(
            f"🚀 **Bot SUI Monitor đã khởi động**\n"
            f"• Theo dõi {len(WATCHED)} ví (1s/kiểm tra)\n"
            f"• RPC: `{RPC_URL}`\n"
            f"• Ví chủ: `{safe_address(withdraw_signer)}`"
        )
    except Exception as e:
        logging.error(f"Lỗi gửi tin nhắn khởi động: {e}")
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
