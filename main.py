import os, requests, discord, asyncio
from discord.ext import commands, tasks

watched_accounts = {
    "Neuter": "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
    "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
    "Tấn Dũng": "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
}

rpc_list = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org",
]
rpc_index = 0

discord_token = os.getenv("DISCORD_TOKEN")
channel_id    = int(os.getenv("DISCORD_CHANNEL_ID"))

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
balance_cache = {}

def get_balance(addr):
    global rpc_index
    try:
        rpc_url = rpc_list[rpc_index % len(rpc_list)]
        payload = {"jsonrpc": "2.0","id":1,"method":"suix_getBalance","params":[addr]}
        r = requests.post(rpc_url, json=payload, timeout=10)
        if r.status_code == 200:
            j = r.json()
            if "result" in j and "totalBalance" in j["result"]:
                return int(j["result"]["totalBalance"])
        rpc_index += 1
    except Exception as e:
        print("RPC error:", e)
        rpc_index += 1
    return None

async def send(msg):
    try:
        ch = await bot.fetch_channel(channel_id)
        await ch.send(msg)
    except Exception as e:
        print("Send error:", e)

@tasks.loop(seconds=5)
async def track():
    for name, addr in watched_accounts.items():
        cur = get_balance(addr)
        if cur is None: continue
        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta = (cur - prev)/1e9
            arrow = "🟢 TĂNG" if delta>0 else "🔴 GIẢM"
            msg = (f"🚨 **{name} thay đổi số dư!**\n"
                   f"{arrow} **{abs(delta):.4f} SUI**\n"
                   f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI")
            await send(msg)
        balance_cache[addr] = cur
        await asyncio.sleep(0.5)

@bot.event
async def on_ready():
    print("Bot online as", bot.user)
    track.start()

@bot.command()
async def ping(ctx): await ctx.send("✅ Bot OK!")
@bot.command()
async def balance(ctx):
    lines=[]
    for n,a in watched_accounts.items():
        b=get_balance(a)
        if b: lines.append(f"💰 {n}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "Err")

bot.run(discord_token)
