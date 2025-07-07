import os
import json
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient

# === Logging config ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# === Env vars ===
RPC_URL        = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN  = os.getenv("DISCORD_TOKEN")
CHANNEL_ID     = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY= os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường cần thiết!")

# === Load watched wallets ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Đã tải {len(WATCHED)} ví từ watched.json")
except Exception as e:
    logging.error(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# === SUI connect ===
try:
    cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
    client = SyncClient(cfg)
    withdraw_signer = str(cfg.active_address)
    logging.info(f"Kết nối SUI thành công! Địa chỉ ví: {withdraw_signer[:10]}...")
except Exception as e:
    logging.critical(f"Lỗi kết nối SUI: {e}")
    raise

# === Discord bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}

def safe_address(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI (SUI) bằng get_gas"""
    try:
        res = client.get_gas(address=addr)
        coins = res.data if hasattr(res, "data") else res
        return sum(int(c.balance) / 1_000_000_000 for c in coins)
    except Exception as e:
        logging.error(f"Lỗi khi kiểm tra số dư {safe_address(addr)}: {e}")
        return -1

def withdraw_all_sui(from_addr: str) -> str | None:
    """Rút hết SUI về ví target (chỉ rút ví có private key - withdraw_signer)"""
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None
    try:
        # Lấy gas object SUI
        res = client.get_gas(address=from_addr)
        coins = res.data if hasattr(res, "data") else res
        if not coins:
            logging.error("Không có SUI (gas object) để rút!")
            return None
        primary_coin = coins[0]
        total = sum(int(c.balance) for c in coins)
        # Trừ 1_000_000 MIST làm fee dự phòng (tùy network bạn chỉnh lại)
        send_amount = total - 1_000_000 if total > 1_000_000 else total
        if send_amount <= 0:
            logging.warning("Không đủ SUI để rút sau khi trừ fee")
            return None
        tx_result = client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=send_amount,
            gas_object=primary_coin.object_id
        )
        return tx_result.tx_digest if hasattr(tx_result, 'tx_digest') else None
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
        return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = get_sui_balance(addr)
            prev = last_balances.get(addr, -1)
            # Thông báo thay đổi số dư
            if balance != prev and prev != -1:
                ch = bot.get_channel(CHANNEL_ID)
                msg = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"🔄 Số dư: `{balance:.6f} SUI` (trước: `{prev:.6f}`)"
                )
                await ch.send(msg)
            last_balances[addr] = balance

            # Rút nếu là ví được bật rút & là ví private key
            if wallet.get("withdraw", False) and balance > 0:
                tx_hash = withdraw_all_sui(addr)
                if tx_hash:
                    ch = bot.get_channel(CHANNEL_ID)
                    await ch.send(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {wallet.get('name', safe_address(addr))}\n"
                        f"Số tiền: `{balance:.6f} SUI`\n"
                        f"TX: `{tx_hash}`"
                    )
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

# --- Lệnh Discord check số dư
@bot.command()
async def balance(ctx, address: str = None):
    """Xem số dư SUI một ví bất kỳ"""
    if not address:
        await ctx.send("Nhập địa chỉ ví!")
        return
    bal = get_sui_balance(address)
    await ctx.send(f"Số dư `{safe_address(address)}`: `{bal:.6f} SUI`")

# === Web server Railway keepalive ===
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
            f"• Ví rút chủ: `{safe_address(withdraw_signer)}`"
        )
    except Exception as e:
        logging.error(f"Lỗi gửi tin nhắn khởi động: {e}")
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
