import os
import json
import logging
import asyncio
from aiohttp import web
from discord.ext import commands, tasks
from pysui import SyncClient, SuiConfig
from pysui.sui.sui_crypto import SuiKeyPair

# === Logging setup ===
logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")

# === Environment Variables ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
SUI_TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, SUI_TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường!")

# === Load watched.json ===
with open("watched.json", "r") as f:
    wallets = json.load(f)

# === Tách danh sách ví theo dõi và ví được rút
WATCHED = {w['address']: w['name'] for w in wallets}
WITHDRAW_ADDR = next((w['address'] for w in wallets if w.get("withdraw")), None)
if not WITHDRAW_ADDR:
    raise RuntimeError("❌ Không có ví nào được gán 'withdraw: true'!")

# === Init client và keypair
cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
client = SyncClient(cfg)
signer_address = str(cfg.active_address)
keypair = SuiKeyPair.from_keystring(SUI_PRIVATE_KEY)

if signer_address != WITHDRAW_ADDR:
    raise RuntimeError(f"⚠️ Private key không khớp với ví withdraw: {signer_address} != {WITHDRAW_ADDR}")

# === Discord bot ===
intents = commands.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# === Lấy số dư
def get_balance(address: str) -> int:
    try:
        result = client.get_all_coins(address=address)
        return sum(int(coin.balance) for coin in result.data)
    except Exception as e:
        logging.error(f"Lỗi khi lấy số dư {address}: {e}")
        return -1

# === Rút toàn bộ
def withdraw_all():
    try:
        coins = client.get_gas(address=signer_address)
        if not coins:
            logging.warning("❌ Không có gas để rút")
            return None

        gas_obj = coins[0]
        amount = get_balance(signer_address)
        if amount <= 0:
            return None

        tx_result = client.transfer_sui(
            signer=keypair,
            recipient=SUI_TARGET_ADDRESS,
            amount=amount,
            gas_object=gas_obj.object_id
        )

        if tx_result.result_data and tx_result.result_data.status.status == "success":
            return tx_result.tx_digest
        else:
            logging.error("❌ Giao dịch thất bại")
    except Exception as e:
        logging.error(f"❌ Withdraw thất bại: {e}")
    return None

# === Theo dõi thay đổi số dư
last_balances = {}

@tasks.loop(seconds=1)
async def monitor():
    for addr, name in WATCHED.items():
        bal = get_balance(addr)
        if addr not in last_balances:
            last_balances[addr] = bal
        if bal != last_balances[addr]:
            ch = await bot.fetch_channel(CHANNEL_ID)
            await ch.send(f"🔍 {name} (`{addr[:8]}...`) có số dư thay đổi: `{bal / 1e9:.4f} SUI`")
            last_balances[addr] = bal

        if addr == WITHDRAW_ADDR and bal > 0:
            tx = withdraw_all()
            ch = await bot.fetch_channel(CHANNEL_ID)
            if tx:
                await ch.send(f"💸 Đã tự động rút `{bal / 1e9:.4f} SUI` từ `{name}` ➜ `{SUI_TARGET_ADDRESS}` · Tx: `{tx}`")
            else:
                await ch.send("⚠️ Không thể rút, kiểm tra log!")

# === Web server (ping Railway) ===
async def handle(request):
    return web.Response(text="Bot is alive.")

async def start_web():
    app = web.Application()
    app.router.add_get("/", handle)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", 8080)))
    await site.start()

@bot.event
async def on_ready():
    logging.info("✅ Bot đã sẵn sàng.")
    ch = await bot.fetch_channel(CHANNEL_ID)
    await ch.send("🟢 Bot đã khởi động và theo dõi số dư các ví!")
    monitor.start()
    bot.loop.create_task(start_web())

bot.run(DISCORD_TOKEN)
