import os
import json
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient, SyncTransaction
from pysui.sui.sui_crypto import SuiKeyPair

# === Logging setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# === Env config ===
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường!")

# === Watched wallets ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Đã tải {len(WATCHED)} ví từ watched.json")
except Exception as e:
    logging.error(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# === SUI connect ===
try:
    cfg = SuiConfig.user_config(
        prv_keys=[SUI_PRIVATE_KEY],
        rpc_url=RPC_URL
    )
    client = SyncClient(cfg)
    keypair = SuiKeyPair.from_b64(SUI_PRIVATE_KEY) if SUI_PRIVATE_KEY.startswith("AAA") else None
    withdraw_signer = str(cfg.active_address)
    logging.info(f"Kết nối SUI thành công! Địa chỉ ví: {withdraw_signer[:10]}...")
except Exception as e:
    logging.critical(f"Lỗi kết nối SUI: {e}")
    raise

# === Discord Bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}  # addr -> float

def safe_address(addr: str) -> str:
    """Ẩn một phần địa chỉ ví để bảo mật"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    try:
        res = client.get_gas(address=addr)
        if hasattr(res, 'data'):
            return sum(int(c.balance) / 1_000_000_000 for c in res.data)
        return 0
    except Exception as e:
        logging.error(f"Lỗi lấy số dư {safe_address(addr)}: {e}")
        return 0

def withdraw_sui(from_addr: str, bal: float) -> str | None:
    try:
        # Chỉ cho phép ví đúng private key mới rút!
        if from_addr != withdraw_signer:
            logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)} (chỉ rút từ ví chủ của bot)")
            return None
        # Lấy gas object
        gas_list = client.get_gas(address=from_addr).data
        if not gas_list:
            logging.warning(f"Không có gas object để rút: {safe_address(from_addr)}")
            return None
        tx = SyncTransaction(client)
        tx.transfer_sui(
            signer=from_addr,
            sui_object_id=gas_list[0].object_id,
            gas_object_id=gas_list[0].object_id,
            recipient=TARGET_ADDRESS,
            amount=int(bal * 1e9)
        )
        result = tx.execute()
        if hasattr(result, "tx_digest"):
            logging.info(f"Đã rút về {TARGET_ADDRESS}: {bal} SUI")
            return result.tx_digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
    return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            bal = get_sui_balance(addr)
            prev = last_balances.get(addr, -1)
            if bal != prev and prev != -1:
                emoji = "🔼" if bal > prev else "🔽"
                diff = bal - prev
                msg = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Số dư: `{bal:.6f} SUI` ({'+' if diff>0 else ''}{diff:.6f})"
                )
                await bot.get_channel(CHANNEL_ID).send(msg)
            last_balances[addr] = bal

            # Auto rút nếu đúng ví chủ và có số dư
            if wallet.get("withdraw", False) and bal > 0 and addr == withdraw_signer:
                tx_hash = withdraw_sui(addr, bal)
                if tx_hash:
                    await bot.get_channel(CHANNEL_ID).send(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {wallet.get('name', safe_address(addr))}\n"
                        f"Số tiền: `{bal:.6f} SUI`\n"
                        f"TX: `{tx_hash}`"
                    )
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

# === Web server cho Railway ===
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