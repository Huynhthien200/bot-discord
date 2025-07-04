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

last_balances = {}  # addr -> balance

def safe_address(addr: str) -> str:
    """Ẩn một phần địa chỉ ví để bảo mật"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI (Mist => SUI)"""
    try:
        res = client.get_gas(address=addr)
        if not hasattr(res, "data") or not res.data:
            return 0
        return sum(int(coin.balance) for coin in res.data) / 1_000_000_000
    except Exception as e:
        logging.error(f"Lỗi lấy số dư {safe_address(addr)}: {e}")
        return 0

def withdraw_sui(from_addr: str, bal: float) -> str | None:
    """Rút toàn bộ SUI về ví mục tiêu"""
    try:
        # Chỉ cho phép ví đúng private key mới rút!
        if from_addr != withdraw_signer:
            logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)} (chỉ rút từ ví chủ của bot)")
            return None

        # Lấy gas object
        gas_list = client.get_gas(address=from_addr)
        if not hasattr(gas_list, "data") or not gas_list.data:
            logging.warning(f"Không có gas object để rút: {safe_address(from_addr)}")
            return None

        # GỌI transfer_sui (pysui >= 0.85.0)
        tx = client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=int(bal * 1_000_000_000),
            gas_object=gas_list.data[0].object_id
        )
        if hasattr(tx, "tx_digest"):
            logging.info(f"Đã rút về {TARGET_ADDRESS}: {bal} SUI")
            return tx.tx_digest

    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
    return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = get_sui_balance(addr)
            prev = last_balances.get(addr, None)
            # Nếu số dư thay đổi, gửi Discord
            if prev is not None and abs(balance - prev) > 0:
                emoji = "🔼" if (balance - prev) > 0 else "🔽"
                await bot.get_channel(CHANNEL_ID).send(
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Số dư: `{balance:.6f} SUI` ({'+' if (balance-prev)>0 else ''}{balance-prev:.6f})"
                )
            last_balances[addr] = balance

            # Nếu cấu hình withdraw và có tiền, thực hiện rút
            if wallet.get("withdraw", False) and balance > 0:
                tx_hash = withdraw_sui(addr, balance)
                if tx_hash:
                    await bot.get_channel(CHANNEL_ID).send(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {wallet.get('name', safe_address(addr))}\n"
                        f"Số tiền: `{balance:.6f} SUI`\n"
                        f"TX: `{tx_hash}`"
                    )
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

@bot.command()
async def xemso(ctx, address: str):
    bal = get_sui_balance(address)
    await ctx.send(f"Số dư {safe_address(address)}: `{bal:.6f} SUI`")

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
            f"• Theo dõi {len(WATCHED)} ví (5s/kiểm tra)\n"
            f"• RPC: `{RPC_URL}`\n"
            f"• Ví chủ: `{safe_address(withdraw_signer)}`"
        )
    except Exception as e:
        logging.error(f"Lỗi gửi tin nhắn khởi động: {e}")
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
