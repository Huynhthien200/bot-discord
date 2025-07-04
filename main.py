import os
import json
import logging
import asyncio
import random
import discord
from discord.ext import commands, tasks
from aiohttp import web
from pysui import SuiConfig
from pysui.sui.sui_clients.sync_client import SuiClient
from pysui.sui.sui_types import SuiString

# === Logging Setup ===
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s",
    handlers=[
        logging.FileHandler("sui_bot.log"),
        logging.StreamHandler()
    ]
)

# === RPC Endpoints ===
RPC_ENDPOINTS = [
    "https://fullnode.mainnet.sui.io",
    "https://rpc-mainnet.suiscan.xyz",
    "https://sui-mainnet-endpoint.blockvision.org"
]

# === Environment Variables ===
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
CHANNEL_ID = int(os.getenv("DISCORD_CHANNEL_ID", "0"))
SUI_PRIVATE_KEY = os.getenv("SUI_PRIVATE_KEY")
TARGET_ADDRESS = os.getenv("SUI_TARGET_ADDRESS")

if not all([DISCORD_TOKEN, CHANNEL_ID, SUI_PRIVATE_KEY, TARGET_ADDRESS]):
    raise RuntimeError("❌ Missing required environment variables!")

# === Watched Wallets ===
try:
    with open("watched.json", "r") as f:
        WATCHED = json.load(f)
    logging.info(f"Loaded {len(WATCHED)} wallets from watched.json")
except Exception as e:
    logging.error(f"Error reading watched.json: {e}")
    WATCHED = []

# === SUI Connection Manager ===
class SuiManager:
    def __init__(self):
        self.current_rpc = random.choice(RPC_ENDPOINTS)
        self.client = self._create_client()
        
    def _create_client(self):
        try:
            cfg = SuiConfig.user_config(
                prv_keys=[SUI_PRIVATE_KEY],
                rpc_url=self.current_rpc
            )
            client = SuiClient(cfg)
            # Test connection immediately
            client.get_coin_objects(SuiString(str(cfg.active_address)), limit=1)
            return client
        except Exception as e:
            logging.error(f"Error creating client with RPC {self.current_rpc}: {e}")
            return None
            
    def switch_rpc(self):
        old_rpc = self.current_rpc
        remaining_rpcs = [rpc for rpc in RPC_ENDPOINTS if rpc != old_rpc]
        if not remaining_rpcs:
            return False
            
        self.current_rpc = random.choice(remaining_rpcs)
        self.client = self._create_client()
        if self.client:
            logging.info(f"Switched from RPC {old_rpc} to {self.current_rpc}")
            return True
        return False

sui_manager = SuiManager()

# === Discord Bot ===
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="!", intents=intents)

last_balances = {}
rpc_errors = 0
MAX_RPC_ERRORS = 3

def safe_address(addr: str) -> str:
    """Obfuscate wallet address for security"""
    return f"{addr[:6]}...{addr[-4:]}" if addr else "unknown"

async def get_sui_balance(addr: str) -> float:
    """Get SUI balance with proper error handling"""
    global rpc_errors
    
    for attempt in range(3):
        try:
            if not sui_manager.client:
                raise Exception("SUI client not initialized")
                
            coins = sui_manager.client.get_coin_objects(
                owner=SuiString(addr),
                coin_type="0x2::sui::SUI",
                limit=10
            )
            
            if coins and hasattr(coins, 'data'):
                rpc_errors = 0
                return sum(int(c.balance) for c in coins.data) / 1_000_000_000
            return 0
        except Exception as e:
            logging.warning(f"RPC {sui_manager.current_rpc} error (attempt {attempt + 1}): {str(e)[:200]}")
            if attempt == 2 or rpc_errors >= MAX_RPC_ERRORS:
                if not sui_manager.switch_rpc():
                    logging.error("Failed to switch RPC")
            await asyncio.sleep(1)
    
    logging.error(f"Failed to get balance for {safe_address(addr)}")
    return -1

async def withdraw_sui(from_addr: str) -> str | None:
    """Withdraw all SUI to target address"""
    if not sui_manager.client:
        logging.error("SUI client not available")
        return None

    try:
        # Get coins for the address
        coins = sui_manager.client.get_coin_objects(
            owner=SuiString(from_addr),
            coin_type="0x2::sui::SUI",
            limit=1
        )
        
        if not coins.data:
            logging.warning(f"No coins found for {safe_address(from_addr)}")
            return None

        # Get precise balance
        balance = sum(int(c.balance) for c in coins.data) / 1_000_000_000
        if balance <= 0.001:  # Skip small balances
            return None

        # Execute transfer
        tx_result = sui_manager.client.transfer_sui(
            signer=from_addr,
            recipient=TARGET_ADDRESS,
            amount=int(balance * 1_000_000_000),
            gas_budget=10_000_000
        )

        if tx_result and tx_result.result_data:
            tx_digest = tx_result.result_data.tx_digest
            logging.info(f"✅ Sent {balance:.6f} SUI from {safe_address(from_addr)}")
            
            try:
                channel = bot.get_channel(CHANNEL_ID)
                await channel.send(
                    f"💸 **Transaction Successful**\n"
                    f"• From: `{safe_address(from_addr)}`\n"
                    f"• To: `{safe_address(TARGET_ADDRESS)}`\n"
                    f"• Amount: `{balance:.6f} SUI`\n"
                    f"• TX Hash: `{tx_digest}`\n"
                    f"• RPC: `{sui_manager.current_rpc}`"
                )
                return tx_digest
            except Exception as e:
                logging.error(f"Discord send error: {e}")
    except Exception as e:
        logging.error(f"❌ Withdrawal error: {e}")
        try:
            channel = bot.get_channel(CHANNEL_ID)
            await channel.send(
                f"❌ Failed transaction from `{safe_address(from_addr)}`\n"
                f"• Error: `{str(e)[:200]}`\n"
                f"• RPC: `{sui_manager.current_rpc}`"
            )
        except Exception as e:
            logging.error(f"Failed to send error notification: {e}")
    
    return None

@tasks.loop(seconds=1)
async def monitor_wallets():
    for wallet in WATCHED:
        addr = wallet["address"]
        try:
            balance = await get_sui_balance(addr)
            if balance < 0:  # Skip if error
                continue
                
            last_balance = last_balances.get(addr, -1)

            # Notify balance changes
            if balance != last_balance and last_balance != -1:
                change = balance - last_balance
                emoji = "🔼" if change > 0 else "🔽"
                message = (
                    f"**{wallet.get('name', 'Unnamed')}** ({safe_address(addr)})\n"
                    f"{emoji} Balance: `{balance:.6f} SUI` ({'↑' if change > 0 else '↓'} {abs(change):.6f})\n"
                    f"• RPC: `{sui_manager.current_rpc}`"
                )
                try:
                    await bot.get_channel(CHANNEL_ID).send(message)
                except Exception as e:
                    logging.error(f"Balance notification error: {e}")

            last_balances[addr] = balance

            # Auto-withdraw if enabled
            if wallet.get("withdraw", False) and balance > 0.001:
                await withdraw_sui(addr)
                
        except Exception as e:
            logging.error(f"Error processing wallet {safe_address(addr)}: {e}")

# === Web Server for Railway ===
async def health_check(request):
    return web.Response(text=f"🟢 Bot running | Tracking {len(WATCHED)} wallets | RPC: {sui_manager.current_rpc}")

async def start_web_server():
    app = web.Application()
    app.router.add_get("/", health_check)
    runner = web.AppRunner(app)
    await runner.setup()
    site = web.TCPSite(runner, "0.0.0.0", int(os.getenv("PORT", "8080")))
    await site.start()

@bot.event
async def on_ready():
    logging.info(f"Discord bot ready: {bot.user.name}")
    try:
        channel = bot.get_channel(CHANNEL_ID)
        await channel.send(
            f"🚀 **SUI Monitor Started**\n"
            f"• Tracking {len(WATCHED)} wallets (1s interval)\n"
            f"• Current RPC: `{sui_manager.current_rpc}`\n"
            f"• Main wallet: `{safe_address(str(sui_manager.client.config.active_address))}`\n"
            f"• Target wallet: `{safe_address(TARGET_ADDRESS)}`"
        )
    except Exception as e:
        logging.error(f"Startup message error: {e}")
    
    monitor_wallets.start()
    await start_web_server()

if __name__ == "__main__":
    bot.run(DISCORD_TOKEN)
