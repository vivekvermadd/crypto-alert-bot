import asyncio
import logging
import os
import aiohttp
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ConversationHandler, MessageHandler, ContextTypes, filters
)

logging.basicConfig(level=logging.INFO)

TOKEN = os.getenv("BOT_TOKEN") or "PASTE_YOUR_BOT_TOKEN_HERE"

EXCHANGES = {
    "BINANCE": "https://api.binance.com/api/v3/ticker/price?symbol={}",
    "BYBIT": "https://api.bybit.com/v5/market/tickers?category=spot&symbol={}",
    "HTX": "https://api.huobi.pro/market/detail/merged?symbol={}",
    "KUCOIN": "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={}",
    "GATE": "https://api.gateio.ws/api/v4/spot/tickers?currency_pair={}",
    "BITMART": "https://api-cloud.bitmart.com/spot/v1/ticker?symbol={}"
}

alerts = {}
ADD_PAIR, ADD_EXCHANGE, ADD_DIRECTION, ADD_PRICE = range(4)

async def fetch_price(exchange, pair):
    try:
        url = EXCHANGES[exchange].format(pair)
        async with aiohttp.ClientSession() as session:
            async with session.get(url, timeout=10) as r:
                data = await r.json()

                if exchange == "BINANCE":
                    return float(data["price"])
                if exchange == "BYBIT":
                    return float(data["result"]["list"][0]["lastPrice"])
                if exchange == "HTX":
                    return float(data["tick"]["close"])
                if exchange == "KUCOIN":
                    return float(data["data"]["price"])
                if exchange == "GATE":
                    return float(data[0]["last"])
                if exchange == "BITMART":
                    return float(data["data"]["tickers"][0]["last_price"])
    except:
        return None

async def price_watcher(app):
    while True:
        for user_id in list(alerts.keys()):
            for alert in alerts[user_id][:]:
                price = await fetch_price(alert["exchange"], alert["pair"])
                if price is None:
                    continue

                hit = (alert["type"] == "ABOVE" and price >= alert["price"]) or \
                      (alert["type"] == "BELOW" and price <= alert["price"])

                if hit:
                    msg = f"🚨 ALERT TRIGGERED!\n{alert['pair']} ({alert['exchange']})\nCurrent: {price}\nTarget: {alert['type']} {alert['price']}"
                    await app.bot.send_message(chat_id=user_id, text=msg)
                    alerts[user_id].remove(alert)

        await asyncio.sleep(5)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /addalert to create price alerts.\nUse /listalerts to view.\nUse /deletealerts to remove.")

async def addalert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Enter Trading Pair (example: BTCUSDT):")
    return ADD_PAIR

async def get_pair(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["pair"] = update.message.text.upper()
    buttons = [[InlineKeyboardButton(x, callback_data=x)] for x in EXCHANGES]
    await update.message.reply_text("Select Exchange:", reply_markup=InlineKeyboardMarkup(buttons))
    return ADD_EXCHANGE

async def get_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["exchange"] = query.data
    buttons = [[InlineKeyboardButton("ABOVE", callback_data="ABOVE"), InlineKeyboardButton("BELOW", callback_data="BELOW")]]
    await query.edit_message_text("Trigger when price:", reply_markup=InlineKeyboardMarkup(buttons))
    return ADD_DIRECTION

async def get_direction(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.edit_message_text("Enter target price:")
    return ADD_PRICE

async def get_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    price = float(update.message.text)

    alert = {
        "pair": context.user_data["pair"],
        "exchange": context.user_data["exchange"],
        "type": context.user_data["type"],
        "price": price
    }

    alerts.setdefault(user_id, []).append(alert)
    await update.message.reply_text(f"✅ Alert Added:\n{alert['pair']} | {alert['exchange']} | {alert['type']} {alert['price']}")
    return ConversationHandler.END

async def listalerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    if user_id not in alerts or not alerts[user_id]:
        await update.message.reply_text("No active alerts.")
        return

    msg = "📋 Your Alerts:\n"
    for i, a in enumerate(alerts[user_id], 1):
        msg += f"{i}. {a['pair']} | {a['exchange']} | {a['type']} {a['price']}\n"
    await update.message.reply_text(msg)

async def deletealerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.message.from_user.id
    alerts[user_id] = []
    await update.message.reply_text("❌ All alerts deleted.")

def main():
    app = Application.builder().token(TOKEN).build()

    conv = ConversationHandler(
        entry_points=[CommandHandler("addalert", addalert)],
        states={
            ADD_PAIR: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_pair)],
            ADD_EXCHANGE: [CallbackQueryHandler(get_exchange)],
            ADD_DIRECTION: [CallbackQueryHandler(get_direction)],
            ADD_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_price)]
        },
        fallbacks=[]
    )

    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("listalerts", listalerts))
    app.add_handler(CommandHandler("deletealerts", deletealerts))

    loop = asyncio.get_event_loop()
    loop.create_task(price_watcher(app))

    app.run_polling()

if __name__ == "__main__":
    main()
