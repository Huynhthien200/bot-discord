# =========================================================
#  Discord SUI Wallet Tracker – Render Web Service FREE
#  Chu kỳ kiểm tra: 1 giây
# =========================================================
import os, requests, discord, asyncio
from discord.ext import commands, tasks
from flask import Flask
from threading import Thread

# ---------- Ví cần theo dõi ----------
watched_accounts = {
    "Neuter":        "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
    "Khiêm Nguyễn":  "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
    "Tấn Dũng":      "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
}

# ---------- RPC dự phòng ----------
rpc_list  = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org"
]
rpc_index = 0

# ---------- Token & Channel ----------
discord_token = os.getenv("DISCORD_TOKEN")            # bắt buộc
channel_id    = int(os.getenv("DISCORD_CHANNEL_ID"))  # bắt buộc

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

balance_cache: dict[str, int] = {}

def get_balance(addr: str) -> int | None:
    """Gọi RPC lấy totalBalance của ví Sui"""
    global rpc_index
    try:
        rpc_url = rpc_list[rpc_index % len(rpc_list)]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "suix_getBalance",
            "params": [addr]
        }
        r = requests.post(rpc_url, json=payload, timeout=10)
        if r.status_code == 200:
            j = r.json()
            if "result" in j and "totalBalance" in j["result"]:
                return int(j["result"]["totalBalance"])
        # nếu lỗi → thử RPC tiếp theo
        rpc_index += 1
    except Exception as e:
        print("RPC error:", e)
        rpc_index += 1
    return None

async def send(msg: str):
    try:
        ch = await bot.fetch_channel(channel_id)
        await ch.send(msg)
    except Exception as e:
        print("Send error:", e)

@tasks.loop(seconds=1)          # ⏱️ quét mỗi 1 giây
async def track():
    for name, addr in watched_accounts.items():
        cur = get_balance(addr)
        if cur is None:
            continue

        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta  = (cur - prev) / 1e9
            arrow  = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            await send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI"
            )
        balance_cache[addr] = cur
        await asyncio.sleep(0.1)        # giảm tải RPC & Gateway

@bot.event
async def on_ready():
    print("🤖 Logged in as", bot.user)
    track.start()

@bot.command()
async def ping(ctx):
    await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):
    lines = []
    for n, a in watched_accounts.items():
        b = get_balance(a)
        if b: lines.append(f"💰 {n}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

# ---------- Flask keep-alive ----------
app = Flask(__name__)

@app.route('/')
def home(): return "✅ Discord SUI bot is alive!"

def run_web():
    port = int(os.getenv("PORT", "8080"))   # Render sets $PORT
    app.run(host="0.0.0.0", port=port)

# ---------- Khởi chạy ----------
if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()  # mở cổng HTTP để Render hài lòng
    bot.run(discord_token)
