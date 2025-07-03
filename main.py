import os
import json
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient, SuiTransaction

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
SUI_TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, SUI_TARGET_ADDRESS]):
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
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI (đơn vị SUI, float)."""
    try:
        res = client.get_gas(address=addr)
        if hasattr(res, "data"):
            coins = res.data
        else:
            coins = res  # fallback (tuỳ pysui version)
        return sum(int(coin.balance) / 1_000_000_000 for coin in coins)
    except Exception as e:
        logging.error(f"Lỗi lấy số dư {safe_address(addr)}: {e}")
        return 0.0

def withdraw_sui(from_addr: str, recipient: str, bal: float) -> str | None:
    """Rút toàn bộ SUI về ví mục tiêu"""
    try:
        gas_list = client.get_gas(address=from_addr).data
        if not gas_list:
            logging.warning(f"Không có gas object để rút: {safe_address(from_addr)}")
            return None
        tx = SuiTransaction(client)
        tx.transfer_sui(
            from_coin=gas_list[0].object_id,
            recipient=recipient,
            amount=int(bal * 1e9)
        )
        result = tx.execute()  # KHÔNG CẦN signer!
        logging.info(f"Đã rút về {recipient}: {bal} SUI")
        return result.tx_digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
        return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            cur_bal = get_sui_balance(addr)
            prev_bal = last_balances.get(addr, None)
            # Thông báo thay đổi số dư
            if prev_bal is not None and abs(cur_bal - prev_bal) > 0:
                emoji = "🔼" if cur_bal > prev_bal else "🔽"
                await bot.get_channel(CHANNEL_ID).send(
                    f"{emoji} **{wallet.get('name','?')}** ({safe_address(addr)}) thay đổi số dư: `{cur_bal:.4f} SUI` ({'+' if cur_bal-prev_bal>=0 else ''}{cur_bal-prev_bal:.4f})"
                )
            last_balances[addr] = cur_bal
            # Tự động rút nếu bật
            if wallet.get("withdraw", False) and addr.lower() == withdraw_signer.lower() and cur_bal > 0:
                tx_hash = withdraw_sui(addr, SUI_TARGET_ADDRESS, cur_bal)
                if tx_hash:
                    await bot.get_channel(CHANNEL_ID).send(
                        f"💸 **Đã tự động rút** `{cur_bal:.4f} SUI` về ví `{safe_address(SUI_TARGET_ADDRESS)}`\nTX: `{tx_hash}`"
                    )
        except Exception as e:
            logging.error(f"Lỗi ví {safe_address(addr)}: {e}")

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