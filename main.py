import os
import json
import logging
import asyncio
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SyncClient, SuiConfig

# === Logging setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# === Env vars ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường!")

# === Load watched wallets ===
with open("watched.json", "r") as f:
    WATCHED = json.load(f)

# === Sui setup ===
cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
client = SyncClient(cfg)
withdraw_signer = str(cfg.active_address)
keypair = cfg.active_address_keypair

# === Discord bot ===
intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}

def get_balance(addr: str) -> int:
    try:
        res = client.get_all_coins(address=addr)
        return sum(int(c.balance) for c in res.data)
    except Exception as e:
        logging.error(f"RPC lỗi khi lấy số dư ví {addr}: {e}")
        return -1

def withdraw_all(from_addr: str) -> str | None:
    try:
        if from_addr != withdraw_signer:
            logging.warning(f"⚠️ Không thể rút từ ví {from_addr} vì không khớp ví rút tiền")
            return None
        gas_objs = client.get_gas(address=from_addr)
        if not gas_objs:
            logging.warning("⚠️ Không tìm thấy gas object")
            return None
        gas = gas_objs[0]
        amt = get_balance(from_addr)
        if amt <= 0:
            return None
        ptb = client.transfer_sui(
            signer=keypair,
            recipient=TARGET_ADDRESS,
            amount=amt,
            gas_object=gas.object_id
        )
        digest = ptb.tx_digest
        logging.info(f"Đã rút toàn bộ từ {from_addr} → {TARGET_ADDRESS} | TX: {digest}")
        return digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút tiền: {e}")
        return None

@tasks.loop(seconds=1)
async def monitor():
    for wallet in WATCHED:
        addr = wallet["address"]
        is_withdraw = wallet.get("withdraw", False)

        balance = get_balance(addr)
        last = last_balances.get(addr, -1)

        if balance != last:
            logging.info(f"📈 Ví {addr[:10]}... có số dư thay đổi: {balance}")
            last_balances[addr] = balance
            ch = await bot.fetch_channel(CHANNEL_ID)
            await ch.send(f"📈 Ví `{addr[:10]}...` thay đổi số dư: `{balance/1e9:.4f} SUI`")

        if balance > 0 and is_withdraw:
            logging.info(f"💸 Đủ điều kiện rút từ {addr}")
            tx = withdraw_all(addr)
            if tx:
                ch = await bot.fetch_channel(CHANNEL_ID)
                await ch.send(f"💸 Đã tự động rút `{balance/1e9:.4f} SUI` về ví `{TARGET_ADDRESS[:10]}...`\nTX: `{tx}`")

# === Web server for Railway keep-alive ===
async def handle(_):
    return web.Response(text="✅ Bot is running.")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

@bot.event
async def on_ready():
    logging.info("✅ Bot Discord đã sẵn sàng.")
    await bot.get_channel(CHANNEL_ID).send(f"🟢 Bot đã chạy và đang theo dõi {len(WATCHED)} ví.")
    monitor.start()
    bot.loop.create_task(start_web())

bot.run(DISCORD_TOKEN)
