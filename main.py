import asyncio
import aiohttp
import sqlite3
import os
from datetime import datetime
from telegram import Update
from telegram.ext import ApplicationBuilder, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN")

DB = "alerts.db"
CHECK_INTERVAL = 10  # seconds (default)

EXCHANGES = {
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

async def get_price(exchange, pair):
    url = EXCHANGES[exchange].format(pair)
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

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        pair = context.args[0].upper()
        exchange = context.args[1].upper()
        condition = context.args[2].upper()
        target = float(context.args[3])
    except:
        await update.message.reply_text("Usage: /add BTCUSDT BINANCE ABOVE 72000")
        return

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("INSERT INTO alerts (user_id,pair,exchange,condition,target,last_state) VALUES (?,?,?,?,?,?)",
                (update.effective_user.id, pair, exchange, condition, target, "NONE"))
    con.commit()
    con.close()

    await update.message.reply_text(f"Alert added: {pair} {exchange} {condition} {target}")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("SELECT id,pair,exchange,condition,target FROM alerts WHERE user_id=?",
                (update.effective_user.id,))
    rows = cur.fetchall()
    con.close()

    if not rows:
        await update.message.reply_text("No active alerts.")
        return

    msg = "Your Alerts:\n"
    for r in rows:
        msg += f"{r[0]}. {r[1]} {r[2]} {r[3]} {r[4]}\n"

    await update.message.reply_text(msg)

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        alert_id = int(context.args[0])
    except:
        await update.message.reply_text("Usage: /remove 1")
        return

    con = sqlite3.connect(DB)
    cur = con.cursor()
    cur.execute("DELETE FROM alerts WHERE id=?", (alert_id,))
    con.commit()
    con.close()

    await update.message.reply_text("Alert removed.")

async def price_watcher(app):
    while True:
        con = sqlite3.connect(DB)
        cur = con.cursor()
        cur.execute("SELECT id,user_id,pair,exchange,condition,target,last_state FROM alerts")
        alerts = cur.fetchall()

        for a in alerts:
            aid, user_id, pair, exchange, cond, target, last_state = a
            try:
                price = await get_price(exchange, pair)
            except:
                continue

            now_state = "ABOVE" if price > target else "BELOW"

            if cond == "ABOVE" and last_state == "BELOW" and price > target:
                await app.bot.send_message(user_id,
                    f"🚨 {pair} on {exchange} crossed ABOVE {target}\nPrice: {price}\nTime: {datetime.now()}")
            elif cond == "BELOW" and last_state == "ABOVE" and price < target:
                await app.bot.send_message(user_id,
                    f"🚨 {pair} on {exchange} crossed BELOW {target}\nPrice: {price}\nTime: {datetime.now()}")

            cur.execute("UPDATE alerts SET last_state=? WHERE id=?", (now_state, aid))
            con.commit()

        con.close()
        await asyncio.sleep(CHECK_INTERVAL)

async def main():
    init_db()
    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("remove", remove))

    asyncio.create_task(price_watcher(app))
    await app.run_polling()

if __name__ == "__main__":
    asyncio.run(main())
