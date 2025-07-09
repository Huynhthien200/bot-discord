import os
import json
import logging
import discord
from discord.ext import commands, tasks
from suipy import SuiWallet

# --- Logging ---
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

# --- ENV ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")
CHECK_INTERVAL = int(os.getenv("CHECK_INTERVAL", "1"))  # giây

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_TARGET_ADDRESS]):
    raise RuntimeError("❌ Missing environment variables!")

# --- Load watched.json ---
with open("watched.json", "r") as f:
    WATCHED = json.load(f)

# --- Prepare wallets (key: address, value: SuiWallet) ---
WALLETS = {}
for w in WATCHED:
    if "private_key" in w and w["private_key"]:
        wallet = SuiWallet.from_private_key(w["private_key"])
        WALLETS[w["address"]] = wallet
    else:
        logging.error(f"Ví {w.get('name', w['address'])} thiếu private_key, sẽ bỏ qua.")

# --- Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# --- Trạng thái số dư cũ
last_balances = {}

async def send_discord(msg: str):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(msg)
    else:
        logging.error("Không tìm thấy kênh Discord!")

@tasks.loop(seconds=CHECK_INTERVAL)
async def monitor_wallets():
    for w in WATCHED:
        address = w["address"]
        name = w.get("name", address[:8])
        wallet = WALLETS.get(address)

        if not wallet:
            continue  # Bỏ qua ví không có private_key hợp lệ

        try:
            # Check balance
            balance = wallet.get_sui_balance()
            prev_balance = last_balances.get(address, None)
            if prev_balance is not None and balance != prev_balance:
                emoji = "🔼" if balance > prev_balance else "🔽"
                chg = balance - prev_balance
                await send_discord(
                    f"**{name}** ({address[:8]}...)\n"
                    f"{emoji} Số dư: `{balance:.6f} SUI` ({'+' if chg>0 else ''}{chg:.6f})"
                )
            last_balances[address] = balance

            # Tự động rút nếu có cờ withdraw và có tiền > 0.01 SUI
            if w.get("withdraw", False) and balance > 0.01:
                amount = balance - 0.01
                try:
                    tx_digest = wallet.transfer_sui(to_address=SUI_TARGET_ADDRESS, amount=amount)
                    await send_discord(
                        f"💸 **Đã rút tự động**\n"
                        f"Ví: {name}\n"
                        f"Số tiền: `{amount:.6f} SUI`\n"
                        f"TX: `{tx_digest}`"
                    )
                except Exception as e:
                    await send_discord(
                        f"❌ **Rút tiền thất bại cho ví {name} ({address[:8]}...)**\nLỗi: {e}"
                    )
        except Exception as e:
            logging.error(f"Lỗi với ví {address[:8]}...: {e}")

@bot.event
async def on_ready():
    logging.info("Bot đã sẵn sàng!")
    await send_discord(f"🚀 **Bot SUI Monitor đã khởi động, check mỗi {CHECK_INTERVAL}s**")
    monitor_wallets.start()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
