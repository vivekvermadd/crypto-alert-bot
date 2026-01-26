import logging
import asyncio
import requests
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    ContextTypes,
    ConversationHandler,
)
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from plyer import notification
from playsound import playsound

# ------------------- CONFIG -------------------
TELEGRAM_TOKEN = "YOUR_TELEGRAM_BOT_TOKEN"
POLL_INTERVAL = 1  # seconds
EXCHANGES = ["BINANCE", "BYBIT", "KUCOIN", "HTX", "GATE", "BITMART"]
ALERTS = []  # Store active alerts

# ------------------- LOGGING -------------------
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s', level=logging.INFO
)

# ------------------- PRICE FETCHING -------------------
async def fetch_binance(symbol):
    url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol}"
    resp = requests.get(url, timeout=5)
    return float(resp.json()["price"])

async def fetch_bybit(symbol):
    url = f"https://api.bybit.com/v2/public/tickers?symbol={symbol}"
    resp = requests.get(url, timeout=5)
    return float(resp.json()["result"][0]["last_price"])

async def fetch_kucoin(symbol):
    url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol}"
    resp = requests.get(url, timeout=5)
    return float(resp.json()["data"]["price"])

async def fetch_htx(symbol):
    url = f"https://api.hadax.com/market/ticker?symbol={symbol}"
    resp = requests.get(url, timeout=5)
    return float(resp.json()["ticker"]["last"])

async def fetch_gate(symbol):
    url = f"https://api.gateio.ws/api2/1/ticker/{symbol}"
    resp = requests.get(url, timeout=5)
    return float(resp.json()["last"])

async def fetch_bitmart(symbol):
    url = f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol}"
    resp = requests.get(url, timeout=5)
    return float(resp.json()["data"]["tickers"][0]["last_price"])

FETCH_FUNCTIONS = {
    "BINANCE": fetch_binance,
    "BYBIT": fetch_bybit,
    "KUCOIN": fetch_kucoin,
    "HTX": fetch_htx,
    "GATE": fetch_gate,
    "BITMART": fetch_bitmart,
}

# ------------------- ALERT CHECKER -------------------
async def price_checker(context: ContextTypes.DEFAULT_TYPE):
    for alert in ALERTS:
        exchange = alert["exchange"]
        symbol = alert["symbol"]
        price_type = alert["type"]
        target_price = alert["price"]
        fetch_fn = FETCH_FUNCTIONS.get(exchange)
        if not fetch_fn:
            continue
        try:
            current_price = await fetch_fn(symbol)
            logging.info(f"{exchange} {symbol} price={current_price}")

            triggered = (
                price_type == "ABOVE" and current_price >= target_price
            ) or (
                price_type == "BELOW" and current_price <= target_price
            )

            if triggered and not alert.get("notified"):
                msg = f"🚨 Alert! {symbol} on {exchange} is {price_type} {target_price}\nCurrent: {current_price}"
                await context.bot.send_message(chat_id=alert["chat_id"], text=msg)

                # Push notification with sound
                notification.notify(
                    title=f"{exchange} {symbol} Alert",
                    message=msg,
                    timeout=5
                )
                # Optional sound (change path if needed)
                # playsound("alert.mp3")  

                alert["notified"] = True
            elif not triggered:
                alert["notified"] = False  # re-arm when price goes back
        except Exception as e:
            logging.error(f"Error fetching {symbol} on {exchange}: {e}")

# ------------------- TELEGRAM HANDLERS -------------------
EXCHANGE, SYMBOL, TYPE, PRICE = range(4)

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [[InlineKeyboardButton(ex, callback_data=ex)] for ex in EXCHANGES]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select Exchange:", reply_markup=reply_markup)
    return EXCHANGE

async def select_exchange(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["exchange"] = query.data
    await query.edit_message_text(f"Selected Exchange: {query.data}\nEnter SYMBOL (like BTCUSDT):")
    return SYMBOL

async def enter_symbol(update: Update, context: ContextTypes.DEFAULT_TYPE):
    context.user_data["symbol"] = update.message.text.upper()
    keyboard = [
        [InlineKeyboardButton("ABOVE", callback_data="ABOVE"),
         InlineKeyboardButton("BELOW", callback_data="BELOW")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Select Alert Type:", reply_markup=reply_markup)
    return TYPE

async def select_type(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    context.user_data["type"] = query.data
    await query.edit_message_text(f"Selected Type: {query.data}\nEnter TARGET PRICE:")
    return PRICE

async def enter_price(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        price = float(update.message.text)
        context.user_data["price"] = price
        alert = {
            "chat_id": update.message.chat_id,
            "exchange": context.user_data["exchange"],
            "symbol": context.user_data["symbol"],
            "type": context.user_data["type"],
            "price": price,
            "notified": False,
        }
        ALERTS.append(alert)
        await update.message.reply_text(f"✅ Alert added:\n{alert}")
    except ValueError:
        await update.message.reply_text("Please enter a valid number for price.")
        return PRICE
    return ConversationHandler.END

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not ALERTS:
        await update.message.reply_text("No active alerts.")
    else:
        msg = "\n".join([f"{i+1}. {a['symbol']} {a['type']} {a['price']} on {a['exchange']}" for i, a in enumerate(ALERTS)])
        await update.message.reply_text(f"Active Alerts:\n{msg}")

async def remove_alert(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        idx = int(context.args[0]) - 1
        if 0 <= idx < len(ALERTS):
            removed = ALERTS.pop(idx)
            await update.message.reply_text(f"Removed alert: {removed}")
        else:
            await update.message.reply_text("Invalid alert number.")
    except:
        await update.message.reply_text("Usage: /remove <alert_number>")

async def clear_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ALERTS.clear()
    await update.message.reply_text("All alerts cleared.")

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Cancelled.")
    return ConversationHandler.END

# ------------------- MAIN -------------------
def main():
    app = ApplicationBuilder().token(TELEGRAM_TOKEN).build()

    # Conversation for creating alert
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            EXCHANGE: [CallbackQueryHandler(select_exchange)],
            SYMBOL: [CommandHandler("text", enter_symbol)],
            TYPE: [CallbackQueryHandler(select_type)],
            PRICE: [CommandHandler("text", enter_price)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(conv)

    # Alert management
    app.add_handler(CommandHandler("list", list_alerts))
    app.add_handler(CommandHandler("remove", remove_alert))
    app.add_handler(CommandHandler("clear", clear_alerts))

    # Scheduler
    scheduler = AsyncIOScheduler()
    scheduler.add_job(price_checker, "interval", seconds=POLL_INTERVAL, args=[app])
    scheduler.start()

    logging.info("Production-ready bot started...")
    app.run_polling()

if __name__ == "__main__":
    main()
