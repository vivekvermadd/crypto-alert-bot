# ================================
# TELEGRAM CRYPTO ALERT BOT - FINAL SPOT VERSION
# Exchanges: Binance, Bybit, KuCoin, HTX, Gate.io, BitMart
# Fully live WebSocket feeds, alerts on EVERY CROSS
# ================================

import asyncio
import sqlite3
import json
from dataclasses import dataclass
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
)
import aiohttp
import os
import zlib
import base64

BOT_TOKEN = os.getenv("BOT_TOKEN")

# ================================
# DATABASE SETUP
# ================================
conn = sqlite3.connect("alerts.db", check_same_thread=False)
cursor = conn.cursor()
cursor.execute("""
CREATE TABLE IF NOT EXISTS alerts(
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    exchange TEXT,
    symbol TEXT,
    direction TEXT,
    target REAL,
    last_state TEXT
)
""")
conn.commit()

@dataclass
class Alert:
    id: int
    user_id: int
    exchange: str
    symbol: str
    direction: str
    target: float
    last_state: str

# ================================
# TELEGRAM HANDLERS
# ================================
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Welcome to Crypto Alert Bot!\n"
        "Use /add to create alert, /view to see alerts, /delete to remove.\n\n"
        "Example to add:\n/add BINANCE BTCUSDT ABOVE 50000"
    )

async def add_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        _, exchange, symbol, direction, target = update.message.text.split()
        target = float(target)
        cursor.execute(
            "INSERT INTO alerts(user_id, exchange, symbol, direction, target, last_state) VALUES(?,?,?,?,?,?)",
            (update.message.chat_id, exchange.upper(), symbol.upper(), direction.upper(), target, "UNKNOWN")
        )
        conn.commit()
        await update.message.reply_text(f"✅ Alert Added:\n{symbol} | {exchange.upper()} | {direction.upper()} {target}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}\nUsage: /add EXCHANGE SYMBOL ABOVE/BELOW PRICE")

async def view_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    cursor.execute("SELECT id, exchange, symbol, direction, target FROM alerts WHERE user_id=?", (update.message.chat_id,))
    rows = cursor.fetchall()
    if not rows:
        await update.message.reply_text("No alerts set.")
        return
    msg = "\n".join([f"{r[0]}: {r[2]} | {r[1]} | {r[3]} {r[4]}" for r in rows])
    await update.message.reply_text(msg)

async def delete_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        _, alert_id = update.message.text.split()
        cursor.execute("DELETE FROM alerts WHERE id=? AND user_id=?", (int(alert_id), update.message.chat_id))
        conn.commit()
        await update.message.reply_text(f"Deleted alert {alert_id}")
    except Exception as e:
        await update.message.reply_text(f"Error: {e}\nUsage: /delete ALERT_ID")

# ================================
# EXCHANGE WEBSOCKET FUNCTIONS (SPOT)
# ================================

# ------------------- BINANCE -------------------
async def fetch_binance(symbol: str):
    url = f"wss://stream.binance.com:9443/ws/{symbol.lower()}@trade"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    yield float(data["p"])

# ------------------- BYBIT -------------------
async def fetch_bybit(symbol: str):
    url = "wss://stream.bybit.com/realtime_public"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_json({"op":"subscribe","args":[f"{symbol}.trade"]})
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if "data" in data:
                        yield float(data["data"][0]["price"])

# ------------------- KUCOIN -------------------
async def fetch_kucoin(symbol: str):
    async with aiohttp.ClientSession() as session:
        # Step 1: Get token
        async with session.post("https://api.kucoin.com/api/v1/bullet-public") as r:
            resp = await r.json()
            endpoint = resp["data"]["instanceServers"][0]["endpoint"]
            token = resp["data"]["token"]
        url = f"{endpoint}?token={token}"
        async with session.ws_connect(url) as ws:
            await ws.send_json({"id":1,"type":"subscribe","topic":f"/market/ticker:{symbol}","response":True})
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if "data" in data and "price" in data["data"]:
                        yield float(data["data"]["price"])

# ------------------- HTX (Huobi Spot) -------------------
async def fetch_htx(symbol: str):
    url = "wss://api.huobi.pro/ws"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            # Subscribe to ticker
            await ws.send_json({
                "sub": f"market.{symbol.lower()}.ticker",
                "id": "id1"
            })
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    raw = msg.data
                    # Huobi sends compressed message
                    decompressed = zlib.decompress(raw, 16+zlib.MAX_WBITS)
                    data = json.loads(decompressed)
                    if "tick" in data:
                        yield float(data["tick"]["close"])

# ------------------- GATE.IO -------------------
async def fetch_gate(symbol: str):
    url = "wss://api.gateio.ws/ws/v4/"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_json({
                "time": 0,
                "channel": "spot.tickers",
                "event": "subscribe",
                "payload": [symbol.upper()]
            })
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if "result" in data and isinstance(data["result"], list):
                        for r in data["result"]:
                            if r["currency_pair"].upper() == symbol.upper():
                                yield float(r["last"])

# ------------------- BITMART -------------------
async def fetch_bitmart(symbol: str):
    url = f"wss://ws-manager-compress.bitmart.com/api?protocol=1.1"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            await ws.send_json({"op":"subscribe","args":[f"spot/ticker:{symbol}"]})
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    if "data" in data and "last_price" in data["data"]:
                        yield float(data["data"]["last_price"])

# ------------------- EXCHANGE MAP -------------------
EXCHANGE_WS_MAP = {
    "BINANCE": fetch_binance,
    "BYBIT": fetch_bybit,
    "KUCOIN": fetch_kucoin,
    "HTX": fetch_htx,
    "GATE": fetch_gate,
    "BITMART": fetch_bitmart
}

# ================================
# ALERT ENGINE
# ================================
async def handle_alert(alert: Alert, ws_func):
    async for price in ws_func(alert.symbol):
        crossed = False
        if alert.direction == "ABOVE":
            if alert.last_state != "ABOVE" and price >= alert.target:
                crossed = True
                alert.last_state = "ABOVE"
            elif price < alert.target:
                alert.last_state = "BELOW"
        else:
            if alert.last_state != "BELOW" and price <= alert.target:
                crossed = True
                alert.last_state = "BELOW"
            elif price > alert.target:
                alert.last_state = "ABOVE"
        cursor.execute("UPDATE alerts SET last_state=? WHERE id=?", (alert.last_state, alert.id))
        conn.commit()
        if crossed:
            try:
                await bot.send_message(
                    chat_id=alert.user_id,
                    text=f"⚡ ALERT TRIGGERED\n{alert.symbol} | {alert.exchange}\n"
                         f"{alert.direction} {alert.target}\nCurrent Price: {price}"
                )
            except Exception as e:
                print(f"Failed to send alert: {e}")
        await asyncio.sleep(0.1)

async def alert_checker():
    while True:
        cursor.execute("SELECT * FROM alerts")
        alerts = [Alert(*r) for r in cursor.fetchall()]
        tasks = []
        for a in alerts:
            ws_func = EXCHANGE_WS_MAP.get(a.exchange)
            if ws_func:
                tasks.append(handle_alert(a, ws_func))
        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(1)

# ================================
# RUNNER
# ================================
async def main_runner():
    global bot
    bot = ApplicationBuilder().token(BOT_TOKEN).build()
    bot.add_handler(CommandHandler("start", start))
    bot.add_handler(CommandHandler("add", add_alert))
    bot.add_handler(CommandHandler("view", view_alerts))
    bot.add_handler(CommandHandler("delete", delete_alert))

    asyncio.create_task(alert_checker())
    await bot.run_polling()

# ================================
# ENTRY POINT
# ================================
if __name__ == "__main__":
    asyncio.run(main_runner())
