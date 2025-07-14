import os
import json
import requests
import asyncio
import discord

# === ENVIRONMENT ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
RPC_URL = os.getenv("RPC_URL", "https://rpc-mainnet.suiscan.xyz/")

if not all([DISCORD_TOKEN, CHANNEL_ID]):
    raise RuntimeError("❌ Thiếu biến môi trường cần thiết!")

# === LOAD WATCHED WALLETS ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
except Exception as e:
    print(f"Lỗi đọc watched.json: {e}")
    WATCHED = []

# === DISCORD BOT ===
intents = discord.Intents.default()
intents.message_content = True
bot = discord.Client(intents=intents)

# === HÀM LẤY SỐ DƯ SUI (CÓ XỬ LÝ LỖI RPC) ===
def get_sui_balance(address):
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "suix_getBalance",
        "params": [address, "0x2::sui::SUI"]
    }
    try:
        r = requests.post(RPC_URL, json=payload, timeout=3).json()
        balance = r.get("result", {}).get("totalBalance", None)
        if balance is not None:
            return int(balance) / 1_000_000_000
        else:
            print(f"⚠️ RPC không trả về totalBalance cho {address[:8]}..., response: {r}")
    except Exception as e:
        print(f"❌ Lỗi khi kiểm tra số dư {address[:8]}...: {e}")
    return None  # Lỗi thật sự → trả về None

# === BIẾN LƯU TRẠNG THÁI SỐ DƯ CŨ ===
last_balances = {}

# === HÀM GỬI THÔNG BÁO DISCORD ===
async def send_discord(msg):
    await bot.wait_until_ready()
    channel = bot.get_channel(CHANNEL_ID)
    if channel:
        await channel.send(msg)
    else:
        print("❌ Không tìm thấy kênh Discord!")

# === MONITOR LOOP ===
async def monitor_loop():
    await bot.wait_until_ready()
    global last_balances

    # Khởi tạo số dư ban đầu
    for w in WATCHED:
        addr = w["address"]
        name = w.get("name", addr[:8])
        balance = get_sui_balance(addr)
        if balance is None:
            print(f"⚠️ Không lấy được số dư ví `{name}` ban đầu, sẽ thử lại sau.")
        last_balances[addr] = balance  # Có thể là None
    await asyncio.sleep(1)

    print("✅ Bắt đầu theo dõi các ví:", [w.get("name", w["address"][:8]) for w in WATCHED])

    # Vòng lặp theo dõi liên tục
    while True:
        for w in WATCHED:
            addr = w["address"]
            name = w.get("name", addr[:8])

            old = last_balances.get(addr)
            new = get_sui_balance(addr)

            if new is None:
                print(f"⚠️ RPC lỗi cho ví `{name}`, bỏ qua lần này.")
                continue

            if old is None:
                # Ghi nhận số dư lần đầu khi RPC thành công
                last_balances[addr] = new
                print(f"ℹ️ Đã khởi tạo số dư lần đầu cho ví `{name}`: {new:.6f} SUI")
                continue

            if new != old:
                emoji = "🟢" if new > old else "🔴"
                change = new - old
                msg = (
                    f"📢 **Cập nhật số dư ví SUI!**\n"
                    f"──────────────────────────────\n"
                    f"👤 **Tên ví:** `{name}`\n"
                    f"🏷️ **Địa chỉ:** `{addr[:6]}...{addr[-4:]}`\n"
                    f"{emoji} **Số dư:** `{new:,.6f} SUI`\n"
                    f"💸 **Thay đổi:** `{change:+,.6f} SUI`\n"
                    f"──────────────────────────────"
                )
                await send_discord(msg)

            last_balances[addr] = new  # Cập nhật số dư
        await asyncio.sleep(1)

@bot.event
async def on_ready():
    print(f"🤖 Bot đã sẵn sàng! Đang theo dõi {len(WATCHED)} ví...")
    bot.loop.create_task(monitor_loop())

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
