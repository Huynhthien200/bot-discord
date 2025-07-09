import os
import json
import time
import requests
import asyncio
import discord

# --- ENV ---
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")

if not all([DISCORD_TOKEN, CHANNEL_ID]):
    raise RuntimeError("❌ Thiếu biến môi trường!")

# --- Load watched.json ---
with open("watched.json", "r") as f:
    WATCHED = json.load(f)

# --- Discord Bot ---
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# --- Hàm lấy số dư ---
def get_sui_balance(address):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "suix_getBalance",
        "params": [address, "0x2::sui::SUI"]
    }
    try:
        r = requests.post(RPC_URL, json=payload, timeout=10).json()
        if "result" in r and "totalBalance" in r["result"]:
            return int(r["result"]["totalBalance"]) / 1_000_000_000
    except Exception as e:
        print(f"Lỗi khi kiểm tra số dư {address[:8]}...: {e}")
    return 0.0

# --- Lưu số dư lần trước ---
last_balances = {}

async def send_discord(msg):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(msg)
    else:
        print("❌ Không tìm thấy kênh Discord!")

async def monitor_loop():
    await bot.wait_until_ready()
    global last_balances

    # Khởi tạo số dư lần đầu
    for w in WATCHED:
        addr = w["address"]
        last_balances[addr] = get_sui_balance(addr)
    await asyncio.sleep(1)

    while True:
        for w in WATCHED:
            addr = w["address"]
            name = w.get("name", addr[:8])
            old = last_balances.get(addr, 0)
            new = get_sui_balance(addr)
            if new != old:
                emoji = "🔼" if new > old else "🔽"
                change = new - old
                await send_discord(
                    f"**SUI Monitor**: **{name}** ({addr[:8]}...)\n"
                    f"{emoji} Số dư mới: `{new:.6f} SUI` ({'+' if change > 0 else ''}{change:.6f})"
                )
                last_balances[addr] = new
        await asyncio.sleep(1)

@bot.event
async def on_ready():
    print(f"Bot đã sẵn sàng! Đang theo dõi: {[w['name'] for w in WATCHED]}")
    bot.loop.create_task(monitor_loop())

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
