import os
import logging
import asyncio
from discord.ext import commands, tasks
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair
from aiohttp import web

# === Logging setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# === Config from environment ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("Thiếu biến môi trường!")

# === Sui client ===
cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
client = SyncClient(cfg)
sender = str(cfg.active_address)
keypair = SuiKeyPair.from_b64(SUI_PRIVATE_KEY)

# === Discord bot ===
intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Get balance via get_all_coins ===
def get_balance(address: str) -> int:
    try:
        result = client.get_all_coins(address=address)
        total = sum(int(coin.balance) for coin in result.data)
        return total
    except Exception as e:
        logging.error("RPC lỗi khi lấy số dư: %s", e)
        return -1

# === Withdraw full balance ===
def withdraw_all():
    try:
        coins = client.get_gas(address=sender)
        if not coins:
            logging.warning("Không tìm thấy gas để rút")
            return None

        gas_object = coins[0]
        amount = get_balance(sender)
        if amount <= 0:
            return None

        ptb = client.transfer_sui(signer=keypair, recipient=TARGET_ADDRESS, amount=amount, gas_object=gas_object.object_id)
        tx_result = ptb.result_data
        if tx_result and tx_result.status and tx_result.status.status == "success":
            return ptb.tx_digest
        else:
            logging.error(f"❌ Tx thất bại: {tx_result.status.error if tx_result.status else 'Không rõ lỗi'}")
    except Exception as e:
        logging.error("Withdraw thất bại: %s", e)
    return None

# === Track and withdraw if balance > 0 ===
@tasks.loop(seconds=10)
async def monitor():
    bal = get_balance(sender)
    if bal > 0:
        tx = withdraw_all()
        ch = await bot.fetch_channel(CHANNEL_ID)
        if tx:
            await ch.send(f"💸 Đã tự động rút `{bal/1e9:.4f} SUI` về ví `{TARGET_ADDRESS[:10]}...` · Tx: `{tx}`")
        else:
            await ch.send("⚠️ Không thể rút, kiểm tra log!")

# === Aiohttp web server (keepalive for Railway) ===
async def handle(request):
    return web.Response(text="Bot is running.")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

@bot.event
async def on_ready():
    logging.info("Bot đã sẵn sàng.")
    await bot.get_channel(CHANNEL_ID).send(f"🟢 Bot đã khởi động và đang theo dõi ví: `{sender}`")
    monitor.start()
    bot.loop.create_task(start_web())

bot.run(DISCORD_TOKEN)