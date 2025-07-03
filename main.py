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

last_balances = {}  # addr -> {coin_type: balance}

def safe_address(addr: str) -> str:
    """Ẩn một phần địa chỉ ví để bảo mật"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

async def get_all_tokens(addr: str):
    """
    Lấy dict thông tin token: {coin_type(str): balance(float, đã chia decimal)}
    """
    try:
        res = await asyncio.to_thread(client.get_gas, address=addr)
        tokens = {}
        for coin in res.data:
            coin_type = coin.coin_type
            # Mặc định decimal 9 cho SUI, các token khác thực tế có thể khác, muốn chuẩn xác thì cần get metadata
            decimal = 9 if "sui::SUI" in coin_type.lower() else 9
            balance = int(coin.balance) / (10 ** decimal)
            tokens.setdefault(coin_type, 0)
            tokens[coin_type] += balance
        return tokens
    except Exception as e:
        logging.error(f"Lỗi lấy token {safe_address(addr)}: {e}")
        return {}

async def get_sui_balance(addr: str) -> float:
    """Lấy số dư SUI chuẩn hóa (gọi từ get_all_tokens)"""
    tokens = await get_all_tokens(addr)
    return tokens.get("0x2::sui::SUI", 0)

async def withdraw_sui(from_addr: str) -> str | None:
    """Rút toàn bộ SUI về ví mục tiêu"""
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None

    try:
        balance = await get_sui_balance(from_addr)
        if balance <= 0:
            return None

        gas_objs = await asyncio.to_thread(client.get_gas, address=from_addr)
        if not gas_objs.data:
            logging.warning(f"⚠️ Không tìm thấy Gas Object cho {safe_address(from_addr)}")
            return None

        tx_result = await asyncio.to_thread(
            client.transfer_sui,
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=int(balance * 1_000_000_000),
            gas_object=gas_objs.data[0].object_id
        )
        return tx_result.tx_digest if hasattr(tx_result, 'tx_digest') else None
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút từ {safe_address(from_addr)}: {e}")
        return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            tokens = await get_all_tokens(addr)
            prev = last_balances.get(addr, {})
            # So sánh thay đổi mỗi loại token
            changes = []
            for coin_type, balance in tokens.items():
                last = prev.get(coin_type, -1)
                if last != -1 and abs(balance - last) > 0:
                    change = balance - last
                    emoji = "🔼" if change > 0 else "🔽"
                    short = "SUI" if "sui::SUI" in coin_type.lower() else coin_type.split("::")[-1]
                    changes.append(
                        f"{emoji} **{short}**: `{balance:.6f}` ({'+' if change>0 else ''}{change:.6f})"
                    )
            # Nếu có thay đổi, gửi lên Discord
            if changes:
                msg = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    + "\n".join(changes)
                )
                await bot.get_channel(CHANNEL_ID).send(msg)

            last_balances[addr] = tokens

            # Rút SUI nếu config yêu cầu và có số dư
            if wallet.get("withdraw", False):
                sui_balance = tokens.get("0x2::sui::SUI", 0)
                if sui_balance > 0:
                    tx_hash = await withdraw_sui(addr)
                    if tx_hash:
                        await bot.get_channel(CHANNEL_ID).send(
                            f"💸 **Đã rút tự động**\n"
                            f"Ví: {wallet.get('name', safe_address(addr))}\n"
                            f"Số tiền: `{sui_balance:.6f} SUI`\n"
                            f"TX: `{tx_hash}`"
                        )
        except Exception as e:
            logging.error(f"Lỗi khi xử lý ví {safe_address(addr)}: {e}")

# --- Command kiểm tra mọi token
@bot.command()
async def xemtokens(ctx, address: str):
    tokens = await get_all_tokens(address)
    if not tokens:
        await ctx.send("Không có token nào hoặc lỗi!")
        return
    msg = f"Tài sản của `{safe_address(address)}`:\n"
    for ct, bal in tokens.items():
        label = "SUI" if "sui::SUI" in ct else ct.split("::")[-1]
        msg += f"- {label}: `{bal:.6f}`\n"
    await ctx.send(msg)

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
