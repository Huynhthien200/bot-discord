import os
import json
import logging
import asyncio
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig, SyncClient
from pysui.sui.sui_crypto import SuiKeyPair
from bech32 import bech32_decode, convertbits
import base64

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
RPC_URL         = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")
DISCORD_TOKEN   = os.getenv("DISCORD_TOKEN")
CHANNEL_ID      = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS  = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Thiếu biến môi trường cần thiết!")

# === Đọc watched.json ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Đã tải {len(WATCHED)} ví từ watched.json")
except Exception as e:
    logging.error(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# === Hàm load keypair từ Bech32 hoặc Base64 ===
def load_keypair(raw: str) -> SuiKeyPair:
    raw = raw.strip()
    # Bech32 suiprivkey...
    if raw.startswith("suiprivkey"):
        hrp, data = bech32_decode(raw)
        if not hrp or hrp != "suiprivkey":
            raise RuntimeError("HRP không hợp lệ")
        key_bytes = bytes(convertbits(data, 5, 8, False))
        b64 = base64.b64encode(key_bytes).decode()
        return SuiKeyPair.from_b64(b64)
    # Ngược lại coi là Base64
    return SuiKeyPair.from_b64(raw)

# === Kết nối SUI và load keypair ===
try:
    cfg = SuiConfig.user_config(prv_keys=[SUI_PRIVATE_KEY], rpc_url=RPC_URL)
    client = SyncClient(cfg)
    keypair = load_keypair(SUI_PRIVATE_KEY)
    withdraw_signer = str(keypair.address)
    logging.info(f"Kết nối SUI thành công! Ví rút: {withdraw_signer[:10]}…")
except Exception as e:
    logging.critical(f"Lỗi kết nối SUI: {e}")
    raise

# === Discord Bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances: dict[str, dict[str, float]] = {}

def safe_address(addr: str) -> str:
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

async def get_all_tokens(addr: str) -> dict[str, float]:
    try:
        res   = await asyncio.to_thread(client.get_all_coins, address=addr)
        coins = res.result_data.data
        tokens: dict[str, float] = {}
        for coin in coins:
            typ = coin.coin_type
            bal = int(coin.balance) / 1e9
            tokens[typ] = tokens.get(typ, 0) + bal
        return tokens
    except Exception as e:
        logging.error(f"Lỗi lấy token {safe_address(addr)}: {e}")
        return {}

async def withdraw_sui(from_addr: str) -> str | None:
    if from_addr != withdraw_signer:
        logging.warning(f"⚠️ Không thể rút từ ví {safe_address(from_addr)}")
        return None

    tokens = await get_all_tokens(from_addr)
    bal = tokens.get("0x2::sui::SUI", 0.0)
    if bal <= 0:
        return None

    gas_res  = await asyncio.to_thread(client.get_gas, address=from_addr)
    gas_list = gas_res.result_data.data
    if not gas_list:
        logging.warning(f"⚠️ Không tìm thấy gas cho {safe_address(from_addr)}")
        return None

    try:
        tx_res = await asyncio.to_thread(
            client.transfer_sui,
            signer=keypair,
            recipient=TARGET_ADDRESS,
            amount=int(bal * 1e9),
            gas_object=gas_list[0].object_id
        )
        return tx_res.tx_digest
    except Exception as e:
        logging.error(f"❌ Lỗi khi rút từ {safe_address(from_addr)}: {e}")
        return None

@tasks.loop(seconds=5)
async def monitor_wallets():
    for w in WATCHED:
        addr = w["address"]
        name = w.get("name", safe_address(addr))
        tokens = await get_all_tokens(addr)
        prev   = last_balances.get(addr, {})

        # Thông báo số dư thay đổi
        changes: list[str] = []
        for typ, bal in tokens.items():
            old = prev.get(typ, -1)
            if old != -1 and abs(bal - old) > 0:
                delta = bal - old
                emoji = "🔼" if delta > 0 else "🔽"
                label = "SUI" if "sui::sui" in typ.lower() else typ.split("::")[-1]
                changes.append(f"{emoji} **{label}** `{bal:.6f}` ({delta:+.6f})")

        if changes:
            msg = f"**{name}** ({safe_address(addr)})\n" + "\n".join(changes)
            await bot.get_channel(CHANNEL_ID).send(msg)

        last_balances[addr] = tokens

        # Tự động rút nếu được bật
        if w.get("withdraw", False):
            sui_bal = tokens.get("0x2::sui::SUI", 0.0)
            if sui_bal > 0:
                tx = await withdraw_sui(addr)
                if tx:
                    await bot.get_channel(CHANNEL_ID).send(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {name}\n"
                        f"Số tiền: `{sui_bal:.6f} SUI`\n"
                        f"TX: `{tx}`"
                    )

@bot.command()
async def xemtokens(ctx, address: str):
    toks = await get_all_tokens(address)
    if not toks:
        return await ctx.send("Không có token hoặc lỗi!")
    msg  = f"Tài sản `{safe_address(address)}`:\n"
    for typ, bal in toks.items():
        label = "SUI" if "sui::sui" in typ.lower() else typ.split("::")[-1]
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
    logging.info(f"Bot đã sẵn sàng: {bot.user.name}")
    await bot.get_channel(CHANNEL_ID).send(
        f"🚀 Bot SUI Monitor khởi động\n"
        f"• Theo dõi {len(WATCHED)} ví\n"
        f"• RPC: {RPC_URL}\n"
        f"• Ví rút: {safe_address(withdraw_signer)}"
    )
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
