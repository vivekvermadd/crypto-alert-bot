# main.py
import asyncio
import json
import logging
from decimal import Decimal

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
    ConversationHandler,
    MessageHandler,
    filters,
)
import websockets

logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

# ----------------- CONFIG -----------------
TOKEN = "YOUR_TELEGRAM_BOT_TOKEN_HERE"

# Exchanges WebSocket endpoints for SPOT
EXCHANGE_WS = {
    "BINANCE": "wss://stream.binance.com:9443/ws/{}@ticker",
    "BYBIT": "wss://stream.bybit.com/realtime_public",
    "KUCOIN": "wss://ws-api.kucoin.com/endpoint",
    "HTX": "wss://api.huobi.pro/ws",
    "GATE": "wss://api.gateio.ws/ws/v4/",
    "BITMART": "wss://ws-manager-compress.bitmart.com/api?protocol=1.1",
}

# ----------------- GLOBALS -----------------
alerts = []  # List of dicts: {'chat_id':..., 'exchange':..., 'symbol':..., 'type':..., 'price':...}
EXCHANGES = ["BINANCE", "BYBIT", "KUCOIN", "HTX", "GATE", "BITMART"]
ALERT_TYPES = ["ABOVE", "BELOW"]

# ----------------- CONVERSATION STATES -----------------
SELECT_EXCHANGE, INPUT_SYMBOL, SELECT_TYPE, INPUT_PRICE = range(4)

# ----------------- BOT COMMANDS -----------------
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Welcome! Use /add to create a price alert.")

async def add_alert_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(ex, callback_data=ex)] for ex in EXCHANGES]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select Exchange:", reply_markup=reply_markup)
    return SELECT_EXCHANGE

async def select_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['exchange'] = query.data
    await query.message.reply_text("Enter trading pair symbol (e.g., BTCUSDT):")
    return INPUT_SYMBOL

async def input_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data['symbol'] = update.message.text.upper()
    keyboard = [[InlineKeyboardButton(t, callback_data=t)] for t in ALERT_TYPES]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select alert type:", reply_markup=reply_markup)
    return SELECT_TYPE

async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data['type'] = query.data
    await query.message.reply_text("Enter target price:")
    return INPUT_PRICE

async def input_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
    except ValueError:
        await update.message.reply_text("Invalid price. Enter a number.")
        return INPUT_PRICE
    context.user_data['price'] = price

    # Save alert
    alert = {
        "chat_id": update.message.chat_id,
        "exchange": context.user_data['exchange'],
        "symbol": context.user_data['symbol'],
        "type": context.user_data['type'],
        "price": context.user_data['price'],
        "triggered": False,
    }
    alerts.append(alert)
    await update.message.reply_text(
        f"✅ Alert Added:\n{alert['symbol']} | {alert['exchange']} | {alert['type']} {alert['price']}"
    )
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Alert creation canceled.")
    return ConversationHandler.END

# ----------------- PRICE FETCHING -----------------
async def fetch_binance(symbol: str):
    url = EXCHANGE_WS["BINANCE"].format(symbol.lower())
    async with websockets.connect(url) as ws:
        data = await ws.recv()
        msg = json.loads(data)
        return float(msg['c'])

async def fetch_bybit(symbol: str):
    async with websockets.connect(EXCHANGE_WS["BYBIT"]) as ws:
        req = {"op":"subscribe","args":[f"instrument_info.100ms.{symbol.upper()}"]}
        await ws.send(json.dumps(req))
        data = await ws.recv()
        msg = json.loads(data)
        if 'data' in msg and len(msg['data'])>0:
            return float(msg['data'][0]['last_price'])
        return None

# NOTE: KuCoin, HTX, Gate, BitMart WebSocket price fetching placeholders
# Full WebSocket implementation will require subscription messages per exchange
async def fetch_kucoin(symbol: str): return None
async def fetch_htx(symbol: str): return None
async def fetch_gate(symbol: str): return None
async def fetch_bitmart(symbol: str): return None

FETCH_FUNCTIONS = {
    "BINANCE": fetch_binance,
    "BYBIT": fetch_bybit,
    "KUCOIN": fetch_kucoin,
    "HTX": fetch_htx,
    "GATE": fetch_gate,
    "BITMART": fetch_bitmart,
}

# ----------------- PRICE CHECKER -----------------
async def price_checker(context: ContextTypes.DEFAULT_TYPE):
    for alert in alerts:
        if alert['triggered']:
            continue
        fetch_func = FETCH_FUNCTIONS.get(alert['exchange'])
        if not fetch_func:
            continue
        try:
            current_price = await fetch_func(alert['symbol'])
        except Exception as e:
            logging.error(f"Error fetching {alert['exchange']} {alert['symbol']}: {e}")
            continue
        if current_price is None:
            continue
        triggered = False
        if alert['type'] == "ABOVE" and current_price > alert['price']:
            triggered = True
        elif alert['type'] == "BELOW" and current_price < alert['price']:
            triggered = True
        if triggered:
            alert['triggered'] = True
            await context.bot.send_message(
                chat_id=alert['chat_id'],
                text=f"⚡ Alert Triggered!\n{alert['symbol']} | {alert['exchange']} | {alert['type']} {alert['price']}\nCurrent Price: {current_price}"
            )

# ----------------- MAIN -----------------
async def main():
    app = ApplicationBuilder().token(TOKEN).build()

    conv_handler = ConversationHandler(
        entry_points=[CommandHandler('add', add_alert_start)],
        states={
            SELECT_EXCHANGE: [CallbackQueryHandler(select_exchange)],
            INPUT_SYMBOL: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_symbol)],
            SELECT_TYPE: [CallbackQueryHandler(select_type)],
            INPUT_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, input_price)],
        },
        fallbacks=[CommandHandler('cancel', cancel)],
    )
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv_handler)

    # JobQueue for price checking every 1 second
    app.job_queue.run_repeating(price_checker, interval=1, first=5)

    await app.start()
    await app.updater.start_polling()
    await app.updater.idle()

if __name__ == "__main__":
    asyncio.run(main())
