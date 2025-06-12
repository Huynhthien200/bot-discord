import requests
import discord
import asyncio
from discord.ext import commands, tasks

# ==== Danh sách ví cần theo dõi ====
watched_accounts = {
    "Neuter": "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
    "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
    "Tấn Dũng": "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
}

# ==== RPC luân phiên ====
rpc_list = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org"
]
rpc_index = 0

# ==== Token & kênh Discord ====
discord_token = "MTM4MjYzMjYxNjgzMzg0NzMwNw.GLVrPv.QrtYx3ZfKwahcOcw1yo8Ym-_g1N2mWuMjRDXY0"  # Đặt token thật vào đây
channel_id = 1382659133450227722           # Đặt ID kênh Discord vào đây

# ==== Intents ====
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

# ==== Bộ nhớ cache số dư ====
balance_cache = {}

# ==== Lấy số dư ====
def get_balance(address):
    global rpc_index
    try:
        rpc_url = rpc_list[rpc_index % len(rpc_list)]
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "suix_getBalance",
            "params": [address]
        }
        res = requests.post(rpc_url, json=payload)
        print(f"📱 {address[:8]}... RPC {rpc_url} → {res.status_code}")
        if res.status_code == 200:
            data = res.json()
            if 'result' in data and 'totalBalance' in data['result']:
                return int(data['result']['totalBalance'])
        else:
            rpc_index += 1
    except Exception as e:
        print(f"❌ RPC lỗi: {e}")
        rpc_index += 1
    return None

# ==== Gửi tin nhắn Discord ====
async def send_discord_message(msg):
    try:
        channel = await bot.fetch_channel(channel_id)
        await channel.send(msg)
    except Exception as e:
        print(f"❗️ Lỗi gửi tin nhắn: {e}")

# ==== Theo dõi ====
@tasks.loop(seconds=1)
async def track_all_balances():
    for label, address in watched_accounts.items():
        current = get_balance(address)
        if current is None:
            continue

        last = balance_cache.get(address)
        print(f"➞ {label}: {current / 1e9:.4f} SUI")

        if last is not None and current != last:
            delta = (current - last) / 1e9
            direction = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            msg = (
                f"🚨 **{label} thay đổi số dư!**\n"
                f"{direction} **{abs(delta):.4f} SUI**\n"
                f"💼 {label}: {last / 1e9:.4f} → {current / 1e9:.4f} SUI"
            )
            await send_discord_message(msg)

        balance_cache[address] = current

# ==== Khi bot sẵn sàng ====
@bot.event
async def on_ready():
    print(f"🤖 Bot đã đăng nhập là: {bot.user}")
    track_all_balances.start()

# ==== Lệnh Discord ====
@bot.command()
async def ping(ctx):
    await ctx.send("✅ Bot đang hoạt động!")

@bot.command()
async def balance(ctx):
    messages = []
    for label, address in watched_accounts.items():
        current = get_balance(address)
        if current:
            messages.append(f"💰 {label}: {current / 1e9:.4f} SUI")
    if messages:
        await ctx.send("\n".join(messages))
    else:
        await ctx.send("⚠️ Không lấy được số dư.")

# ==== Chạy bot ====
bot.run(discord_token)
