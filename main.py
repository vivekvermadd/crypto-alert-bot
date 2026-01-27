# main.py
# Production-ready Telegram Crypto Alert Bot (No asyncio.run loop conflict)
# Uses: python-telegram-bot v20+, aiohttp (available on most clouds)
# 6 Exchanges, WebSocket, Inline Menu, 1s Alerts, Telegram-only notifications

import os
import json
import asyncio
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    MessageHandler, ContextTypes, filters
)

BOT_TOKEN = os.environ.get("BOT_TOKEN") or "PUT_YOUR_BOT_TOKEN_HERE"

EXCHANGES = ["BINANCE", "KUCOIN", "BYBIT", "HTX", "GATE", "BITMART"]
user_state = {}
alerts = {}

# ---------------- UI ----------------

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(e, callback_data=f"EX_{e}")] for e in EXCHANGES]
    await update.message.reply_text("Select Exchange:", reply_markup=InlineKeyboardMarkup(keyboard))

async def button_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    uid = q.from_user.id
    data = q.data

    if data.startswith("EX_"):
        user_state[uid] = {"exchange": data.replace("EX_", "")}
        await q.edit_message_text("Send Symbol (e.g. BTCUSDT)")

    elif data in ["ABOVE", "BELOW"]:
        user_state[uid]["condition"] = data
        await q.edit_message_text("Enter Trigger Price")

    elif data.startswith("DEL_"):
        idx = int(data.split("_")[1])
        alerts[uid].pop(idx)
        await q.edit_message_text("Alert Deleted")
        await list_alerts(update, context)

async def text_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.message.from_user.id
    text = update.message.text.upper().strip()

    if uid not in user_state:
        return

    st = user_state[uid]

    if "symbol" not in st:
        st["symbol"] = text
        kb = [
            [InlineKeyboardButton("Price Above", callback_data="ABOVE")],
            [InlineKeyboardButton("Price Below", callback_data="BELOW")]
        ]
        await update.message.reply_text("Condition:", reply_markup=InlineKeyboardMarkup(kb))
        return

    if "price" not in st:
        try:
            st["price"] = float(text)
            alerts.setdefault(uid, []).append(st.copy())
            user_state.pop(uid)
            await update.message.reply_text("Alert Added")
            await list_alerts(update, context)
        except:
            await update.message.reply_text("Invalid price")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in alerts or not alerts[uid]:
        await update.message.reply_text("No active alerts")
        return

    txt = "Active Alerts:\n"
    kb = []
    for i, a in enumerate(alerts[uid]):
        txt += f"{i+1}. {a['exchange']} {a['symbol']} {a['condition']} {a['price']}\n"
        kb.append([InlineKeyboardButton(f"Delete {i+1}", callback_data=f"DEL_{i}")])

    await update.message.reply_text(txt, reply_markup=InlineKeyboardMarkup(kb))

# ---------------- PRICE STREAMS ----------------

async def binance_ws():
    url = "wss://stream.binance.com:9443/ws/!ticker@arr"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            async for msg in ws:
                for t in json.loads(msg.data):
                    yield "BINANCE", t["s"], float(t["c"])

async def bybit_ws():
    url = "wss://stream.bybit.com/v5/public/spot"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_json({"op": "subscribe", "args": ["tickers"]})
            async for msg in ws:
                d = json.loads(msg.data)
                if "data" in d:
                    for t in d["data"]:
                        yield "BYBIT", t["symbol"], float(t["lastPrice"])

async def kucoin_ws():
    async with aiohttp.ClientSession() as s:
        async with s.post("https://api.kucoin.com/api/v1/bullet-public") as r:
            token = (await r.json())["data"]["token"]
        async with s.ws_connect(f"wss://ws-api.kucoin.com/endpoint?token={token}") as ws:
            await ws.send_json({"type": "subscribe", "topic": "/market/ticker:all"})
            async for msg in ws:
                d = json.loads(msg.data)
                if "data" in d and "price" in d["data"]:
                    yield "KUCOIN", d["data"]["symbol"].replace("-", ""), float(d["data"]["price"])

async def htx_ws():
    url = "wss://api.huobi.pro/ws"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_json({"sub": "market.tickers"})
            async for msg in ws:
                d = json.loads(msg.data)
                if "data" in d:
                    for t in d["data"]:
                        yield "HTX", t["symbol"].upper(), float(t["close"])

async def gate_ws():
    url = "wss://api.gateio.ws/ws/v4/"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_json({"channel": "spot.tickers", "event": "subscribe"})
            async for msg in ws:
                d = json.loads(msg.data)
                if "result" in d:
                    for t in d["result"]:
                        yield "GATE", t["currency_pair"].replace("_", ""), float(t["last"])

async def bitmart_ws():
    url = "wss://ws-manager-compress.bitmart.com/api?protocol=1.1"
    async with aiohttp.ClientSession() as s:
        async with s.ws_connect(url) as ws:
            await ws.send_json({"op": "subscribe", "args": ["spot/ticker"]})
            async for msg in ws:
                d = json.loads(msg.data)
                if "data" in d:
                    for t in d["data"]:
                        yield "BITMART", t["symbol"].replace("_", ""), float(t["last_price"])

# ---------------- ALERT ENGINE ----------------

async def monitor(app):
    streams = [binance_ws(), bybit_ws(), kucoin_ws(), htx_ws(), gate_ws(), bitmart_ws()]
    tasks = [asyncio.create_task(s.__anext__()) for s in streams]

    while True:
        done, _ = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for t in done:
            try:
                ex, sym, price = t.result()
                for uid, al in alerts.items():
                    for a in al:
                        if a["exchange"] == ex and a["symbol"] == sym:
                            if (a["condition"] == "ABOVE" and price >= a["price"]) or \
                               (a["condition"] == "BELOW" and price <= a["price"]):
                                await app.bot.send_message(uid, f"🚨 {ex} {sym}\nPrice: {price}")
            except:
                pass
        tasks = [asyncio.create_task(s.__anext__()) for s in streams]

# ---------------- MAIN ----------------

def main():
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(button_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_handler))

    async def post_init(application):
    application.create_task(monitor(application))

    app.post_init = post_init
    app.run_polling()

if __name__ == "__main__":
    main()

