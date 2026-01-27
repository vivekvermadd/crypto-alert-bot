# main.py
# Production-ready Telegram WebSocket Price Alert Bot (6 Exchanges, Inline UI)
# Exchanges: Binance, KuCoin, Bybit, HTX, Gate, BitMart
# Requirements:
# pip install python-telegram-bot==20.8 websockets aiohttp

import asyncio
import json
import os
import websockets
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ContextTypes,
    filters,
)

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE"

EXCHANGES = ["Binance", "KuCoin", "Bybit", "HTX", "Gate", "BitMart"]
user_states = {}
alerts = {}  # user_id -> list of alerts

# -------------------- TELEGRAM UI --------------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    kb = [[InlineKeyboardButton(ex, callback_data=f"ex_{ex}")] for ex in EXCHANGES]
    await update.message.reply_text("Select Exchange:", reply_markup=InlineKeyboardMarkup(kb))

async def handle_buttons(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("ex_"):
        ex = data.split("_")[1]
        user_states[user_id] = {"exchange": ex}
        await query.edit_message_text("Send trading pair (e.g. BTCUSDT):")

    elif data in ["ABOVE", "BELOW"]:
        user_states[user_id]["condition"] = data
        await query.edit_message_text("Enter trigger price:")

    elif data.startswith("del_"):
        idx = int(data.split("_")[1])
        alerts[user_id].pop(idx)
        await query.edit_message_text("Alert removed.")
        await list_alerts(update, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    text = update.message.text.strip().upper()

    if user_id not in user_states:
        return

    state = user_states[user_id]

    if "symbol" not in state:
        state["symbol"] = text
        kb = [
            [InlineKeyboardButton("Price Above", callback_data="ABOVE")],
            [InlineKeyboardButton("Price Below", callback_data="BELOW")]
        ]
        await update.message.reply_text("Condition:", reply_markup=InlineKeyboardMarkup(kb))
    elif "price" not in state:
        try:
            price = float(text)
            state["price"] = price
            alerts.setdefault(user_id, []).append(state.copy())
            user_states.pop(user_id)
            await update.message.reply_text("Alert added successfully.")
            await list_alerts(update, context)
        except:
            await update.message.reply_text("Invalid price. Enter number.")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if user_id not in alerts or not alerts[user_id]:
        await update.message.reply_text("No active alerts.")
        return

    text = "Your Alerts:\n"
    kb = []
    for i, a in enumerate(alerts[user_id]):
        text += f"{i+1}) {a['exchange']} {a['symbol']} {a['condition']} {a['price']}\n"
        kb.append([InlineKeyboardButton(f"Delete {i+1}", callback_data=f"del_{i}")])

    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

# -------------------- WEBSOCKET PRICE FEEDS --------------------

async def binance_ws():
    uri = "wss://stream.binance.com:9443/ws/!ticker@arr"
    async with websockets.connect(uri) as ws:
        while True:
            data = json.loads(await ws.recv())
            for t in data:
                yield "Binance", t["s"], float(t["c"])

async def kucoin_ws():
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.kucoin.com/api/v1/bullet-public") as r:
            token = (await r.json())["data"]["token"]
            ws_url = f"wss://ws-api.kucoin.com/endpoint?token={token}"
    async with websockets.connect(ws_url) as ws:
        await ws.send(json.dumps({"type": "subscribe", "topic": "/market/ticker:all", "privateChannel": False, "response": True}))
        while True:
            msg = json.loads(await ws.recv())
            if "data" in msg and "price" in msg["data"]:
                yield "KuCoin", msg["data"]["symbol"].replace("-", ""), float(msg["data"]["price"])

async def bybit_ws():
    uri = "wss://stream.bybit.com/v5/public/spot"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": ["tickers"]}))
        while True:
            msg = json.loads(await ws.recv())
            if "data" in msg:
                for t in msg["data"]:
                    yield "Bybit", t["symbol"], float(t["lastPrice"])

async def htx_ws():
    uri = "wss://api.huobi.pro/ws"
    async with websockets.connect(uri) as ws:
        sub = {"sub": "market.tickers", "id": "1"}
        await ws.send(json.dumps(sub))
        while True:
            msg = json.loads(await ws.recv())
            if "data" in msg:
                for t in msg["data"]:
                    yield "HTX", t["symbol"].upper(), float(t["close"])

async def gate_ws():
    uri = "wss://api.gateio.ws/ws/v4/"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"time": 1, "channel": "spot.tickers", "event": "subscribe"}))
        while True:
            msg = json.loads(await ws.recv())
            if "result" in msg:
                for t in msg["result"]:
                    yield "Gate", t["currency_pair"].replace("_", ""), float(t["last"])

async def bitmart_ws():
    uri = "wss://ws-manager-compress.bitmart.com/api?protocol=1.1"
    async with websockets.connect(uri) as ws:
        await ws.send(json.dumps({"op": "subscribe", "args": ["spot/ticker"]}))
        while True:
            msg = json.loads(await ws.recv())
            if "data" in msg:
                for t in msg["data"]:
                    yield "BitMart", t["symbol"].replace("_", ""), float(t["last_price"])

# -------------------- ALERT ENGINE --------------------

async def price_engine(app):
    streams = [
        binance_ws(),
        kucoin_ws(),
        bybit_ws(),
        htx_ws(),
        gate_ws(),
        bitmart_ws()
    ]
    async for exchange, symbol, price in merge_streams(streams):
        for user_id, user_alerts in alerts.items():
            for a in user_alerts:
                if a["exchange"] == exchange and a["symbol"] == symbol:
                    if (a["condition"] == "ABOVE" and price >= a["price"]) or \
                       (a["condition"] == "BELOW" and price <= a["price"]):
                        await app.bot.send_message(
                            chat_id=user_id,
                            text=f"🚨 ALERT!\n{exchange} {symbol}\nPrice: {price}\nCondition: {a['condition']} {a['price']}"
                        )

async def merge_streams(streams):
    tasks = [asyncio.create_task(s.__anext__()) for s in streams]
    while True:
        done, tasks = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for d in done:
            result = d.result()
            yield result
            tasks.add(asyncio.create_task(streams[tasks.index(d)].__anext__()))

# -------------------- MAIN --------------------

async def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(handle_buttons))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))
    asyncio.create_task(price_engine(app))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
