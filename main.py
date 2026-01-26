# FULL FINAL TELEGRAM CRYPTO ALERT BOT (SPOT, 6 EXCHANGES, POLLING, INLINE DROPDOWNS)
# Binance, Bybit, KuCoin, HTX, Gate, BitMart
# 1-second price check, ABOVE/BELOW/BOTH, Add/View/Edit/Delete alerts
# Railway / VPS compatible (Polling, no webhook)
# Save as: main.py

import asyncio
import logging
import sqlite3
import requests
from telegram import (
    Update, InlineKeyboardButton, InlineKeyboardMarkup
)
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, ContextTypes, MessageHandler, filters
)

TOKEN = "8531399639:AAECibyuLpAgjo7vt95byOway_PcxIWUaYg"

logging.basicConfig(level=logging.INFO)

DB = "alerts.db"

SELECT_EXCHANGE, SELECT_SYMBOL, SELECT_TYPE, SELECT_PRICE = range(4)

EXCHANGES = ["BINANCE", "BYBIT", "KUCOIN", "HTX", "GATE", "BITMART"]

# ---------------- DATABASE -----------------
def init_db():
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("""CREATE TABLE IF NOT EXISTS alerts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER,
        exchange TEXT,
        symbol TEXT,
        alert_type TEXT,
        price REAL
    )""")
    conn.commit()
    conn.close()

# ---------------- PRICE FETCHERS ----------------
def fetch_binance(symbol):
    r = requests.get(f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}")
    return float(r.json()["price"])

def fetch_bybit(symbol):
    r = requests.get(f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol}")
    return float(r.json()["result"]["list"][0]["lastPrice"])

def fetch_kucoin(symbol):
    r = requests.get(f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol.replace('USDT','-USDT')}")
    return float(r.json()["data"]["price"])

def fetch_htx(symbol):
    r = requests.get(f"https://api.huobi.pro/market/detail/merged?symbol={symbol.lower()}")
    return float(r.json()["tick"]["close"])

def fetch_gate(symbol):
    r = requests.get(f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol.replace('USDT','_USDT')}")
    return float(r.json()[0]["last"])

def fetch_bitmart(symbol):
    r = requests.get(f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol}")
    return float(r.json()["data"]["tickers"][0]["last_price"])

FETCHERS = {
    "BINANCE": fetch_binance,
    "BYBIT": fetch_bybit,
    "KUCOIN": fetch_kucoin,
    "HTX": fetch_htx,
    "GATE": fetch_gate,
    "BITMART": fetch_bitmart
}

# ---------------- BOT COMMANDS ----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Crypto Alert Bot Ready.\nUse /addalert, /viewalerts, /deletealert")

async def addalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(e, callback_data=e)] for e in EXCHANGES]
    await update.message.reply_text("Select Exchange:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_EXCHANGE

async def select_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["exchange"] = query.data
    await query.message.reply_text("Enter Symbol (e.g. BTCUSDT):")
    return SELECT_SYMBOL

async def select_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["symbol"] = update.message.text.upper()
    keyboard = [
        [InlineKeyboardButton("ABOVE", callback_data="ABOVE")],
        [InlineKeyboardButton("BELOW", callback_data="BELOW")],
        [InlineKeyboardButton("BOTH", callback_data="BOTH")]
    ]
    await update.message.reply_text("Select Alert Type:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SELECT_TYPE

async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.message.reply_text("Enter Target Price:")
    return SELECT_PRICE

async def select_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    price = float(update.message.text)
    exchange = context.user_data["exchange"]
    symbol = context.user_data["symbol"]
    atype = context.user_data["type"]
    chat_id = update.effective_chat.id

    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("INSERT INTO alerts (chat_id, exchange, symbol, alert_type, price) VALUES (?,?,?,?,?)",
              (chat_id, exchange, symbol, atype, price))
    conn.commit()
    conn.close()

    await update.message.reply_text(f"Alert Added:\n{symbol} | {exchange} | {atype} {price}")
    return ConversationHandler.END

async def viewalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, exchange, symbol, alert_type, price FROM alerts WHERE chat_id=?", (update.effective_chat.id,))
    rows = c.fetchall()
    conn.close()

    if not rows:
        await update.message.reply_text("No alerts.")
        return

    msg = "Your Alerts:\n"
    for r in rows:
        msg += f"ID {r[0]}: {r[2]} {r[3]} {r[4]} ({r[1]})\n"
    await update.message.reply_text(msg)

async def deletealert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text("Usage: /deletealert ALERT_ID")
        return
    alert_id = int(context.args[0])
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("DELETE FROM alerts WHERE id=? AND chat_id=?", (alert_id, update.effective_chat.id))
    conn.commit()
    conn.close()
    await update.message.reply_text("Alert Deleted.")

# ---------------- PRICE CHECK LOOP ----------------
async def price_checker(context: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB)
    c = conn.cursor()
    c.execute("SELECT id, chat_id, exchange, symbol, alert_type, price FROM alerts")
    rows = c.fetchall()

    for alert in rows:
        alert_id, chat_id, ex, sym, atype, target = alert
        try:
            price = FETCHERS[ex](sym)
            if atype == "ABOVE" and price > target:
                await context.bot.send_message(chat_id, f"{sym} crossed ABOVE {target}\nCurrent: {price}")
            elif atype == "BELOW" and price < target:
                await context.bot.send_message(chat_id, f"{sym} crossed BELOW {target}\nCurrent: {price}")
            elif atype == "BOTH" and (price > target or price < target):
                await context.bot.send_message(chat_id, f"{sym} crossed {target}\nCurrent: {price}")
        except:
            pass
    conn.close()

# ---------------- MAIN ----------------
def main():
    init_db()
    app = ApplicationBuilder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("addalert", addalert)],
        states={
            SELECT_EXCHANGE: [CallbackQueryHandler(select_exchange)],
            SELECT_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_symbol)],
            SELECT_TYPE: [CallbackQueryHandler(select_type)],
            SELECT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, select_price)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("viewalerts", viewalerts))
    app.add_handler(CommandHandler("deletealert", deletealert))
    app.add_handler(conv)

    app.job_queue.run_repeating(price_checker, interval=1, first=5)

    app.run_polling()

if __name__ == "__main__":
    main()

