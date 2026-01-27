import asyncio
import os
from telegram import Update
from telegram.ext import Application, CommandHandler, ContextTypes

BOT_TOKEN = os.getenv("BOT_TOKEN") or "PUT_YOUR_BOT_TOKEN_HERE"

alerts = {}

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text("Bot is running. Alerts system ready.")

async def add(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0].upper()
        price = float(context.args[1])
        alerts[symbol] = price
        await update.message.reply_text(f"Alert set: {symbol} @ {price}")
    except:
        await update.message.reply_text("Usage: /add BTCUSDT 43000")

async def remove(update: Update, context: ContextTypes.DEFAULT_TYPE):
    try:
        symbol = context.args[0].upper()
        alerts.pop(symbol, None)
        await update.message.reply_text(f"Alert removed: {symbol}")
    except:
        await update.message.reply_text("Usage: /remove BTCUSDT")

async def list_alerts(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not alerts:
        await update.message.reply_text("No alerts set.")
        return
    msg = "\n".join([f"{k} @ {v}" for k, v in alerts.items()])
    await update.message.reply_text(msg)

async def monitor(app: Application):
    while True:
        # Dummy trigger logic (replace later with WebSocket live prices)
        for symbol, target in list(alerts.items()):
            current_price = target + 1  # force trigger for testing

            if current_price >= target:
                await app.bot.send_message(
                    chat_id=list(app.chat_data.keys())[0],
                    text=f"🚨 {symbol} hit {current_price}"
                )
                alerts.pop(symbol)

        await asyncio.sleep(1)

async def post_init(application: Application):
    application.create_task(monitor(application))

def main():
    app = Application.builder().token(BOT_TOKEN).post_init(post_init).build()

    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("add", add))
    app.add_handler(CommandHandler("remove", remove))
    app.add_handler(CommandHandler("list", list_alerts))

    app.run_polling()

if __name__ == "__main__":
    main()
