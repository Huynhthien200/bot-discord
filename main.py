# =========================================================
#  Discord SUI Wallet Tracker  ─  Auto-Withdraw toàn bộ
# =========================================================
# --- shim audioop cho Python 3.13 -------------------------
import sys, types
sys.modules['audioop'] = types.ModuleType('audioop')
# ────────── Super-Dummy stubs: numpy / pandas / sklearn ──────────
import sys, types

class DummyMod(types.ModuleType):
    """Mô-đun giả: chịu mọi thao tác mà không ném lỗi."""
    def __getattr__(self, name):
        fullname = f"{self.__name__}.{name}"
        if fullname in sys.modules:           # nếu sub-module đã có, trả luôn
            return sys.modules[fullname]
        dummy = DummyMod(fullname)            # tạo sub-module mới
        sys.modules[fullname] = dummy
        return dummy

    # Cho phép iterate, len, gọi hàm
    def __iter__(self): return iter(())
    def __len__(self): return 0
    def __call__(self, *_, **__): return self
    def __bool__(self): return False         # trong if …: coi như False
    def __repr__(self): return f"<Dummy {self.__name__}>"

for _pkg in ("numpy", "pandas", "sklearn"):
    sys.modules[_pkg] = DummyMod(_pkg)
# ──────────────────────────────────────────────────────────────────

import os, requests, discord, asyncio
from discord.ext import commands, tasks
from sui_py import SuiAccount, SyncClient, sui_txn         # pip install sui-py
from flask import Flask
from threading import Thread

# ---------- Ví cần theo dõi ----------
watched_accounts = {
    "Neuter":       "0x98101c31bff7ba0ecddeaf79ab4e1cfb6430b0d34a3a91d58570a3eb32160682",
    "Khiêm Nguyễn": "0xfb4dd4169b270d767501b142df7b289a3194e72cbadd1e3a2c30118693bde32c",
    "Tấn Dũng":     "0x5ecb5948c561b62fb6fe14a6bf8fba89d33ba6df8bea571fd568772083993f68",
}

# ---------- RPC danh sách ----------
rpc_list  = [
    "https://rpc-mainnet.suiscan.xyz/",
    "https://sui-mainnet-endpoint.blockvision.org"
]
rpc_index = 0
client    = SyncClient(rpc_list[0])            # sui-py (sync)

# ---------- Token & Channel ----------
discord_token = os.getenv("DISCORD_TOKEN")           # bắt buộc
channel_id    = int(os.getenv("DISCORD_CHANNEL_ID")) # bắt buộc

# ---------- Auto-withdraw (ví nguồn = account.address) ----------
SUI_KEY = os.getenv("SUI_PRIVATE_KEY")               # hex private key
TARGET  = os.getenv("SUI_TARGET_ADDRESS")            # ví đích nhận SUI
assert all([discord_token, channel_id, SUI_KEY, TARGET]), "Thiếu biến môi trường!"

account = SuiAccount.from_private_key(SUI_KEY)

# ---------- Discord bot ----------
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)
balance_cache: dict[str, int] = {}

# ---------- Hàm RPC balance ----------
def get_balance(addr: str) -> int | None:
    global rpc_index
    try:
        rpc_url = rpc_list[rpc_index % len(rpc_list)]
        payload = {"jsonrpc":"2.0","id":1,"method":"suix_getBalance","params":[addr]}
        r = requests.post(rpc_url, json=payload, timeout=10)
        if r.status_code == 200:
            j = r.json()
            if "result" in j and "totalBalance" in j["result"]:
                return int(j["result"]["totalBalance"])
        rpc_index += 1         # nếu lỗi, chuyển RPC khác
    except Exception as e:
        print("RPC error:", e)
        rpc_index += 1
    return None

# ---------- Gửi toàn bộ SUI ----------
def send_all_sui() -> str | None:
    try:
        tx = (
            sui_txn.TransferSui(recipient=TARGET)    # không truyền amount  → rút sạch
            .build_and_sign(account)
        )
        res = client.execute(tx)
        if res.effects.status.status == "success":
            return res.tx_digest
        print("Tx failed:", res)
    except Exception as e:
        print("Send SUI error:", e)
    return None

async def discord_send(msg: str):
    try:
        ch = await bot.fetch_channel(channel_id)
        await ch.send(msg)
    except Exception as e:
        print("Discord send error:", e)

# ---------- Vòng quét 1 giây ----------
@tasks.loop(seconds=1)
async def tracker():
    for name, addr in watched_accounts.items():
        cur = get_balance(addr)
        if cur is None:
            continue

        prev = balance_cache.get(addr)
        if prev is not None and cur != prev:
            delta = (cur - prev) / 1e9
            arrow = "🟢 TĂNG" if delta > 0 else "🔴 GIẢM"
            await discord_send(
                f"🚨 **{name} thay đổi số dư!**\n"
                f"{arrow} **{abs(delta):.4f} SUI**\n"
                f"💼 {name}: {prev/1e9:.4f} → {cur/1e9:.4f} SUI"
            )

            # Auto-withdraw nếu ví nguồn nhận thêm SUI
            if delta > 0 and addr.lower() == account.address.lower():
                tx = send_all_sui()
                if tx:
                    await discord_send(
                        f"💸 **Đã rút toàn bộ SUI** về `{TARGET[:6]}…` \n"
                        f"🔗 Tx: `{tx}`"
                    )

        balance_cache[addr] = cur
        await asyncio.sleep(0.1)            # giảm tải RPC / gateway

@bot.event
async def on_ready():
    print("🤖 Logged in as", bot.user)
    tracker.start()

@bot.command()
async def ping(ctx): await ctx.send("✅ Bot OK!")

@bot.command()
async def balance(ctx):
    lines=[]
    for n,a in watched_accounts.items():
        b=get_balance(a)
        if b: lines.append(f"💰 {n}: {b/1e9:.4f} SUI")
    await ctx.send("\n".join(lines) or "⚠️ RPC lỗi")

# ---------- Flask keep-alive cho Render Web Service ----------
app = Flask(__name__)
@app.route('/')            # để UptimeRobot ping giữ “awake”
def home(): return "✅ Discord SUI bot is alive!"

def run_web():
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)

# ---------- Khởi chạy ----------
if __name__ == "__main__":
    Thread(target=run_web, daemon=True).start()
    bot.run(discord_token)
