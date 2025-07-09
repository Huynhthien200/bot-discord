import os
import json
import logging
import discord
from discord.ext import commands, tasks
from aiohttp import web
import base64
from pysui import SuiConfig, SyncClient

# --- Convert suiprivkey1... sang base64 nếu cần ---
def suiprivkey_to_base64(suipriv: str) -> str:
    try:
        if suipriv and suipriv.startswith("suiprivkey1"):
            from bech32 import bech32_decode, convertbits
            hrp, data = bech32_decode(suipriv)
            if hrp != "suiprivkey":
                raise ValueError("Not a valid SUI bech32 private key")
            key_bytes = bytes(convertbits(data, 5, 8, False))
            return base64.b64encode(key_bytes).decode()
        else:
            return suipriv
    except Exception as e:
        raise RuntimeError(f"Lỗi chuyển đổi suiprivkey1 sang base64: {e}")

# --- ENV ---
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
INTERVAL = int(os.getenv("POLL_INTERVAL", "1"))

SUI_PRIVATE_KEY = suiprivkey_to_base64(SUI_PRIVATE_KEY)

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường cần thiết!")

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
    print("Client class:", client.__class__)  # Kiểm tra đúng là SyncClient
except Exception as e:
    logging.critical(f"Lỗi kết nối SUI: {e}")
    raise

# --- Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}

def safe_address(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

def get_sui_balance(addr: str) -> float:
    try:
        res = client.get_balance(address=addr)
        return int(res.totalBalance) / 1_000_000_000
    except Exception as e:
        logging.error(f"Lỗi khi kiểm tra số dư {safe_address(addr)}: {e}")
        return 0.0

async def withdraw_sui(from_addr: str, value: float) -> str | None:
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)} (chỉ ví chủ được phép rút)")
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
            logging.info(f"[DEBUG] {wallet.get('name', 'Unnamed')} ({safe_address(addr)}): Current={balance:.6f} SUI | Prev={prev:.6f}")

            if prev != -1 and abs(balance - prev) > 1e-9:
                emoji = "🔼" if balance > prev else "🔽"
                diff = balance - prev
                await send_discord(
                    f"🔔 **Số dư thay đổi!**\n"
                    f"Ví: **{wallet.get('name', 'Unnamed')}**\n"
                    f"Địa chỉ: `{safe_address(addr)}`\n"
                    f"{emoji} Số dư mới: `{balance:.6f} SUI` ({'+' if diff>0 else ''}{diff:.6f})"
                )
            last_balances[addr] = balance

            # Chỉ tự động rút tiền nếu ví này có cờ withdraw true **và** là ví chủ
            if wallet.get("withdraw", False) and addr == withdraw_signer and balance > 0.01:
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
    await channel.send(msg)

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
