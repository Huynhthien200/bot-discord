import os
import json
import logging
import asyncio
import random
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig
from pysui.sui.sui_clients.sync_client import SuiClient  # Cập nhật import mới
from pysui.sui.sui_types import SuiString

# === Cấu hình logging ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# === Danh sách RPC luân phiên ===
RPC_ENDPOINTS = [
    "https://fullnode.mainnet.sui.io",
    "https://rpc-mainnet.suiscan.xyz",
    "https://sui-mainnet-rpc.nodereal.io"
]

# === Biến môi trường ===
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

# === Quản lý kết nối SUI ===
class SuiManager:
    def __init__(self):
        self.current_rpc = random.choice(RPC_ENDPOINTS)
        self.client = self._create_client()
        
    def _create_client(self):
        try:
            cfg = SuiConfig.user_config(
                prv_keys=[SUI_PRIVATE_KEY],
                rpc_url=self.current_rpc
            )
            return SuiClient(cfg)
        except Exception as e:
            logging.error(f"Lỗi tạo client với RPC {self.current_rpc}: {e}")
            return None
            
    def switch_rpc(self):
        old_rpc = self.current_rpc
        remaining_rpcs = [rpc for rpc in RPC_ENDPOINTS if rpc != old_rpc]
        if not remaining_rpcs:
            return False
            
        self.current_rpc = random.choice(remaining_rpcs)
        self.client = self._create_client()
        if self.client:
            logging.info(f"Đã chuyển từ RPC {old_rpc} sang {self.current_rpc}")
            return True
        return False

sui_manager = SuiManager()

# === Discord Bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}
rpc_errors = 0
MAX_RPC_ERRORS = 3

def safe_address(addr: str) -> str:
    """Ẩn một phần địa chỉ ví để bảo mật"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

async def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI sử dụng API ổn định"""
    global rpc_errors
    
    for attempt in range(3):
        try:
            if not sui_manager.client:
                raise Exception("Client SUI chưa được khởi tạo")
                
            coins = sui_manager.client.get_gas(addr)
            if coins and coins.result_data:
                rpc_errors = 0
                total = sum(int(c.balance) for c in coins.result_data)
                return total / 1_000_000_000
            return 0
        except Exception as e:
            logging.warning(f"Lỗi RPC {sui_manager.current_rpc} (lần {attempt + 1}): {e}")
            if attempt == 2 or rpc_errors >= MAX_RPC_ERRORS:
                if not sui_manager.switch_rpc():
                    logging.error("Không thể chuyển sang RPC mới")
            await asyncio.sleep(1)
    
    logging.error(f"Không thể lấy số dư cho {safe_address(addr)}")
    return -1

async def withdraw_sui(from_addr: str) -> str | None:
    """Rút toàn bộ SUI về ví mục tiêu"""
    if not sui_manager.client:
        logging.error("Client SUI không khả dụng")
        return None

    if from_addr != str(sui_manager.client.config.active_address):
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None

    try:
        balance = await get_sui_balance(from_addr)
        if balance <= 0.001:
            return None

        tx_result = sui_manager.client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=int(balance * 1_000_000_000),
            gas_budget=10_000_000
        )

        if tx_result and tx_result.result_data:
            tx_digest = tx_result.result_data.tx_digest
            logging.info(f"✅ Đã gửi {balance:.6f} SUI từ {safe_address(from_addr)}")
            
            try:
                channel = bot.get_channel(CHANNEL_ID)
                await channel.send(
                    f"💸 **Giao dịch thành công**\n"
                    f"• Từ: `{safe_address(from_addr)}`\n"
                    f"• Đến: `{safe_address(TARGET_ADDRESS)}`\n"
                    f"• Số lượng: `{balance:.6f} SUI`\n"
                    f"• TX Hash: `{tx_digest}`\n"
                    f"• RPC: `{sui_manager.current_rpc}`"
                )
                return tx_digest
            except Exception as e:
                logging.error(f"Lỗi gửi thông báo Discord: {e}")
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
        try:
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send(
                f"❌ Giao dịch thất bại từ `{safe_address(from_addr)}`\n"
                f"• Lỗi: `{str(e)[:200]}`\n"
                f"• RPC: `{sui_manager.current_rpc}`"
            )
        except Exception as e:
            logging.error(f"Lỗi khi gửi thông báo lỗi: {e}")
    
    return None

@tasks.loop(seconds=1)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = await get_sui_balance(addr)
            if balance < 0:
                continue
                
            last_balance = last_balances.get(addr, -1)

            if balance != last_balance and last_balance != -1:
                change = balance - last_balance
                emoji = "🔼" if change > 0 else "🔽"
                message = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Số dư: `{balance:.6f} SUI` ({'↑' if change > 0 else '↓'} {abs(change):.6f})\n"
                    f"• RPC: `{sui_manager.current_rpc}`"
                )
                try:
                    await bot.get_channel(CHANNEL_ID).send(message)
                except Exception as e:
                    logging.error(f"Lỗi gửi thông báo số dư: {e}")

            last_balances[addr] = balance

            if wallet.get("withdraw", False) and balance > 0.001:
                await withdraw_sui(addr)
                
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

# === Web Server for Railway ===
async def health_check(request):
    return web.Response(text=f"🟢 Bot đang chạy | Theo dõi {len(WATCHED)} ví | RPC: {sui_manager.current_rpc}")

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
            f"• Theo dõi {len(WATCHED)} ví (1s/kiểm tra)\n"
            f"• RPC hiện tại: `{sui_manager.current_rpc}`\n"
            f"• Ví chủ: `{safe_address(str(sui_manager.client.config.active_address))}`\n"
            f"• Ví đích: `{safe_address(TARGET_ADDRESS)}`"
        )
    except Exception as e:
        logging.error(f"Lỗi gửi tin nhắn khởi động: {e}")
    
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
