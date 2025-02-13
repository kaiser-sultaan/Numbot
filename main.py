import discord
from discord.ext import commands
import requests
import asyncio
import os
import re
import logging
from datetime import datetime, timezone, timedelta

# ✅ Enable logging
logging.basicConfig(level=logging.INFO)

# ✅ Fetch environment variables from Railway
API_KEY = os.getenv("API_KEY")
EMAIL = os.getenv("EMAIL")
BOT_TOKEN = os.getenv("BOT_TOKEN")

# ✅ Bot setup
intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix="-", intents=intents)

# ✅ Token cache to avoid re-authentication
CACHE = {}

def generate_bearer_token():
    """Fetch and cache the authentication token."""
    if "token" in CACHE:
        return CACHE["token"]

    headers = {"X-API-KEY": API_KEY, "X-API-USERNAME": EMAIL}
    
    for attempt in range(3):  # ✅ Retry up to 3 times
        try:
            response = requests.post(f"https://www.textverified.com/api/pub/v2/auth", headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()
            CACHE["token"] = data.get("token")
            return CACHE["token"]
        except requests.exceptions.RequestException as e:
            logging.warning(f"Token request failed (attempt {attempt+1}): {e}")
            asyncio.sleep(3)

    return None  # ❌ Failed after 3 attempts

def get_balance():
    """Retrieve the current account balance with retry logic."""
    bearer_token = generate_bearer_token()
    if not bearer_token:
        return "Error"

    headers = {"Authorization": f"Bearer {bearer_token}"}
    for attempt in range(3):  # Retry 3 times
        try:
            response = requests.get(f"https://www.textverified.com/api/pub/v2/account/me", headers=headers, timeout=15)
            response.raise_for_status()
            return response.json().get("currentBalance", "Error")
        except requests.exceptions.RequestException as e:
            logging.warning(f"Balance fetch attempt {attempt+1} failed: {e}")
            asyncio.sleep(3)

    return "Error"

@bot.command()
async def verify(ctx, service_name: str):
    """Request a verification number and sync countdown with TextVerified."""
    bearer_token = generate_bearer_token()
    if not bearer_token:
        await ctx.send("`[ERROR] Failed to authenticate with the API.`")
        return

    headers = {"Authorization": f"Bearer {bearer_token}"}
    balance_before = get_balance()

    json_data = {"serviceName": service_name, "capability": "sms"}

    try:
        response = requests.post(f"https://www.textverified.com/api/pub/v2/verifications", headers=headers, json=json_data, timeout=15)
        response.raise_for_status()
        data = response.json() if response.text else None
    except requests.exceptions.RequestException as e:
        await ctx.send(f"`[ERROR] Verification request failed: {e}`")
        return

    if not data or "href" not in data:
        await ctx.send(f"`[ERROR] Verification request failed. Response: {data}`")
        return

    verification_href = data["href"]
    verification_id = data.get("id")
    balance_after = get_balance()

    # ✅ Wait for number assignment
    number = None
    max_wait_time = 30
    start_time = datetime.now(timezone.utc)

    while not number and (datetime.now(timezone.utc) - start_time).total_seconds() < max_wait_time:
        try:
            response = requests.get(verification_href, headers=headers, timeout=15)
            response.raise_for_status()
            details = response.json()
            number = details.get("number", None)
            ends_at = details.get("endsAt", None)
            if number and ends_at:
                break
        except requests.exceptions.RequestException as e:
            logging.warning(f"Number fetch attempt failed: {e}")
        
        await asyncio.sleep(1)

    if not number or not ends_at:
        await ctx.send("`[ERROR] Number not received. Try again.`")
        return

    expires_at = datetime.fromisoformat(ends_at.replace("Z", "+00:00"))

    embed = discord.Embed(title=f"{service_name.upper()} | VERIFICATION", color=discord.Color.dark_gray())
    embed.add_field(name="Status", value="`Pending...`", inline=False)
    embed.add_field(name="Number", value=f"```{number}```", inline=False)
    embed.add_field(name="Balance Before", value=f"```₹{balance_before}```", inline=True)
    embed.add_field(name="Balance After", value=f"```₹{balance_after}```", inline=True)
    embed.add_field(name="OTP", value="```Waiting for OTP...```", inline=False)

    message = await ctx.send(embed=embed)

    # ✅ Start OTP checking
    await check_otp(ctx, message, verification_id, number, balance_after, service_name)

async def check_otp(ctx, message, verification_id, number, balance, service_name):
    """✅ Runs OTP checking separately from countdown."""
    bearer_token = generate_bearer_token()
    headers = {"Authorization": f"Bearer {bearer_token}"}
    sms_url = f"https://www.textverified.com/api/pub/v2/sms?ReservationId={verification_id}"

    timeout = datetime.now(timezone.utc) + timedelta(minutes=5)

    while datetime.now(timezone.utc) < timeout:
        try:
            response = requests.get(sms_url, headers=headers, timeout=15)
            response.raise_for_status()
            data = response.json()

            if data.get("messages"):
                latest_sms = data["messages"][-1]["message"]
                otp_match = re.findall(r"\b\d{4,8}\b", latest_sms)
                otp = otp_match[-1] if otp_match else "No OTP found"

                embed = discord.Embed(title=f"{service_name.upper()} | OTP RECEIVED", color=discord.Color.green())
                embed.add_field(name="Verification Status", value="`Completed ✅`", inline=False)
                embed.add_field(name="Number", value=f"```{number}```", inline=False)
                embed.add_field(name="Balance Remaining", value=f"```₹{balance}```", inline=False)
                embed.add_field(name="OTP", value=f"```{otp}```", inline=False)
                await message.edit(embed=embed)
                return

        except requests.exceptions.RequestException as e:
            logging.warning(f"[WARNING] API request failed: {e}")
            await asyncio.sleep(5)

        await asyncio.sleep(5)

@bot.event
async def on_ready():
    print(f"[INFO] Bot is online as {bot.user}")
    while True:
        await asyncio.sleep(60)  # Keeps the bot process alive

bot.run(BOT_TOKEN)
          
