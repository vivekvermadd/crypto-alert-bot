import os
import sqlite3
import asyncio
import aiohttp
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

BOT_TOKEN = os.getenv("BOT_TOKEN")
DB = "alerts.db"
CHECK_INTERVAL = 10

PAIR, EXCHANGE, CONDITION, PRICE = range(4)

EXCHANGES = ["BINANCE", "BYBIT", "HTX", "KUCOIN", "GATE", "BITMART"]

API_URLS = {
    "BINANCE": "https://api.binance.com/api/v3/ticker/price?symbol={}",
    "BYBIT": "https://api.bybit.com/v2/public/tickers?symbol={}",
    "HTX": "https://api.huobi.pro/market/detail/merged?symbol={}",
    "KUCOIN": "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={}",
    "GATE": "https://api.gateio.ws/api/v4/spot/tickers?currency_pair={}",
    "BITMART": "https://api-cloud.bitmart.com/spot/v1/ticker?symbol={}"
}

def init_db():
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS alerts (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            pair TEXT,
            exchange TEXT,
            condition TEXT,
            target REAL,
            last_state TEXT
        )
    """)
    con.commit()
    con.close()

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "👋 Welcome to Crypto Alert Bot\n\n"
        "Commands:\n"
        "/add - Add new alert (button based)\n"
        "/alerts - View & delete alerts"
    )

async def add_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter Trading Pair (e.g. BTCUSDT):")
    return PAIR

async def get_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pair"] = update.message.text.upper()
    keyboard = [[InlineKeyboardButton(e, callback_data=e)] for e in EXCHANGES]
    await update.message.reply_text("Select Exchange:", reply_markup=InlineKeyboardMarkup(keyboard))
    return EXCHANGE

async def get_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["exchange"] = query.data

    keyboard = [[
        InlineKeyboardButton("ABOVE", callback_data="ABOVE"),
        InlineKeyboardButton("BELOW", callback_data="BELOW")
    ]]
    await query.edit_message_text("Select Condition:", reply_markup=InlineKeyboardMarkup(keyboard))
    return CONDITION

async def get_condition(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["condition"] = query.data
    await query.edit_message_text("Enter Price Level:")
    return PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
    except:
        await update.message.reply_text("Invalid price. Try again.")
        return PRICE

    data = context.user_data
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("""
        INSERT INTO alerts (user_id, pair, exchange, condition, target, last_state)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (update.effective_user.id, data["pair"], data["exchange"],
          data["condition"], price, "NONE"))
    con.commit()
    con.close()

    await update.message.reply_text(
        f"✅ Alert Added:\n{data['pair']} | {data['exchange']} | {data['condition']} {price}"
    )
    return ConversationHandler.END

async def fetch_price(exchange, pair):
    url = API_URLS[exchange].format(pair)
    async with aiohttp.ClientSession() as session:
        async with session.get(url, timeout=10) as r:
            data = await r.json()
            if exchange == "BINANCE":
                return float(data["price"])
            elif exchange == "BYBIT":
                return float(data["result"][0]["last_price"])
            elif exchange == "HTX":
                return float(data["tick"]["close"])
            elif exchange == "KUCOIN":
                return float(data["data"]["price"])
            elif exchange == "GATE":
                return float(data[0]["last"])
            elif exchange == "BITMART":
                return float(data["data"]["tickers"][0]["last_price"])

async def run_price_watcher(context: ContextTypes.DEFAULT_TYPE):
    app = context.application

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT id,user_id,pair,exchange,condition,target,last_state FROM alerts")
    rows = cur.fetchall()

    for row in rows:
        aid, uid, pair, exchange, cond, target, last_state = row
        try:
            price = await fetch_price(exchange, pair)
        except:
            continue

        now_state = "ABOVE" if price > target else "BELOW"

        if cond == "ABOVE" and last_state == "BELOW" and now_state == "ABOVE":
            await app.bot.send_message(uid, f"🚨 {pair} ({exchange}) crossed ABOVE {target}\nPrice: {price}")

        if cond == "BELOW" and last_state == "ABOVE" and now_state == "BELOW":
            await app.bot.send_message(uid, f"🚨 {pair} ({exchange}) crossed BELOW {target}\nPrice: {price}")

        cur.execute("UPDATE alerts SET last_state=? WHERE id=?", (now_state, aid))
        con.commit()

    con.close()


async def alerts_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT id,pair,exchange,condition,target FROM alerts WHERE user_id=?",
                (update.effective_user.id,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("No alerts set.")
        return

    for r in rows:
        text = f"{r[0]}) {r[1]} | {r[2]} | {r[3]} {r[4]}"
        keyboard = [[InlineKeyboardButton("❌ Delete", callback_data=f"DEL_{r[0]}")]]
        await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def delete_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    alert_id = int(query.data.split("_")[1])

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    con.commit()
    con.close()

    await query.edit_message_text("🗑 Alert deleted.")

def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("add", add_start)],
        states={
            PAIR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pair)],
            EXCHANGE: [CallbackQueryHandler(get_exchange)],
            CONDITION: [CallbackQueryHandler(get_condition)],
            PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)],
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("alerts", alerts_list))
    app.add_handler(CallbackQueryHandler(delete_alert, pattern="^DEL_"))
    app.add_handler(conv)

    # START BACKGROUND PRICE CHECKER (correct way)
    app.job_queue.run_repeating(run_price_watcher, interval=10, first=5)

    app.run_polling()


if __name__ == "__main__":
    main()
