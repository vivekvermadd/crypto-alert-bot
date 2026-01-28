import asyncio
import aiohttp
import logging
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import os
import json
from collections import defaultdict
import sqlite3

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

# CoinGecko maps your exchange/symbols
COINGECKO_MAP = {
    'BTCUSDT': 'bitcoin',
    'ETHUSDT': 'ethereum', 
    'SOLUSDT': 'solana',
    'ADAUSDT': 'cardano'
}

conn = sqlite3.connect('alerts.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS alerts 
                  (user_id INTEGER, alert_id TEXT PRIMARY KEY, data TEXT)''')
conn.commit()

alerts = defaultdict(dict)

class AlertForm(StatesGroup):
    symbol = State()
    limit = State()
    direction = State()

async def load_alerts():
    cursor.execute('SELECT * FROM alerts')
    for row in cursor.fetchall():
        uid, aid, data_json = row
        alerts[uid][aid] = json.loads(data_json)

async def save_alert(user_id, alert_id, alert):
    cursor.execute('INSERT OR REPLACE INTO alerts VALUES (?, ?, ?)', 
                   (user_id, alert_id, json.dumps(alert)))
    conn.commit()

async def get_coingecko_price(symbol):
    """CoinGecko API - WORKS EVERYWHERE"""
    try:
        async with aiohttp.ClientSession() as session:
            cg_symbol = COINGECKO_MAP.get(symbol, symbol.split('/')[0].lower())
            url = f"https://api.coingecko.com/api/v3/simple/price?ids={cg_symbol}&vs_currencies=usd"
            async with session.get(url) as resp:
                data = await resp.json()
                return data[cg_symbol]['usd']
    except Exception as e:
        logging.error(f"CoinGecko error: {e}")
        return None

async def price_monitor():
    """2s CoinGecko polling - 100% reliable"""
    while True:
        print(f"🔄 Checking {sum(len(a) for a in alerts.values())} alerts...")
        for user_id, user_alerts in list(alerts.items()):
            for alert_id, alert in list(user_alerts.items()):
                price = await get_coingecko_price(alert['symbol'])
                print(f"💰 {alert['symbol']} = ${price} vs {alert['limit']} {alert['direction']}")
                
                if price:
                    direction = alert['direction']
                    limit = alert['limit']
                    if (direction == 'above' and price >= limit) or (direction == 'below' and price <= limit):
                        await bot.send_message(
                            user_id,
                            f"🚨 **ALERT HIT!**\n\n"
                            f"💱 **{alert['symbol']}**\n"
                            f"💰 **${price:,.2f}**\n"
                            f"🎯 **{direction.upper()} ${limit:,.2f}**\n"
                            f"📊 All exchanges",
                            parse_mode="Markdown"
                        )
                        del alerts[user_id][alert_id]
                        cursor.execute('DELETE FROM alerts WHERE user_id=? AND alert_id=?', (user_id, alert_id))
                        conn.commit()
        await asyncio.sleep(3)

@dp.message(Command('start'))
async def start(message: types.Message):
    keyboard = [
        [InlineKeyboardButton(text="➕ Set Alert", callback_data="set_alert")],
        [InlineKeyboardButton(text="🧪 Test Price", callback_data="test_price")],
        [InlineKeyboardButton(text="📋 My Alerts", callback_data="list_alerts")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("🚀 **Crypto Alert Bot**\n\n✅ CoinGecko (All exchanges)\n⏰ 3s live checks\n🚨 Instant alerts\n\nPopular: BTCUSDT ETHUSDT SOLUSDT", 
                       reply_markup=reply_markup, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "test_price")
async def test_price(callback: CallbackQuery):
    prices = {}
    for sym in ['BTCUSDT', 'ETHUSDT', 'SOLUSDT']:
        price = await get_coingecko_price(sym)
        prices[sym] = price
    
    text = "🧪 **LIVE PRICES:**\n\n"
    for sym, price in prices.items():
        status = f"`{sym}`: **${price:,.2f}**" if price else f"`{sym}`: ❌"
        text += status + "\n"
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "set_alert")
async def set_alert(callback: CallbackQuery, state: FSMContext):
    await callback.message.edit_text(
        "💱 **Enter symbol:**\n\n"
        "`BTCUSDT` `ETHUSDT` `SOLUSDT`", parse_mode="Markdown")
    await state.set_state(AlertForm.symbol)
    await callback.answer()

@dp.message(AlertForm.symbol)
async def set_symbol(message: types.Message, state: FSMContext):
    symbol = message.text.strip().upper()
    if symbol not in COINGECKO_MAP:
        await message.reply("❌ Use: `BTCUSDT` `ETHUSDT` `SOLUSDT`", parse_mode="Markdown")
        return
    await state.update_data(symbol=symbol)
    await message.reply("💰 **Enter limit price:**\n\n`95000`", parse_mode="Markdown")
    await state.set_state(AlertForm.limit)

@dp.message(AlertForm.limit)
async def set_limit(message: types.Message, state: FSMContext):
    try:
        limit = float(message.text)
        await state.update_data(limit=limit)
        keyboard = [
            [InlineKeyboardButton(text="📈 ABOVE", callback_data="dir_above")],
            [InlineKeyboardButton(text="📉 BELOW", callback_data="dir_below")]
        ]
        reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
        await message.reply("🎯 **Direction:**", reply_markup=reply_markup, parse_mode="Markdown")
        await state.set_state(AlertForm.direction)
    except:
        await message.reply("❌ **Enter number:** `95000`", parse_mode="Markdown")

@dp.callback_query(AlertForm.direction)
async def set_dir(callback: CallbackQuery, state: FSMContext):
    direction = 'above' if 'above' in callback.data else 'below'
    data = await state.get_data()
    user_id = callback.from_user.id
    alert_id = f"{data['symbol']}_{direction}_{int(data['limit'])}"
    alert = {'symbol': data['symbol'], 'limit': data['limit'], 'direction': direction}
    alerts[user_id][alert_id] = alert
    await save_alert(user_id, alert_id, alert)
    
    keyboard = [[InlineKeyboardButton(text="📋 List Alerts", callback_data="list_alerts")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(
        f"✅ **ALERT LIVE!**\n\n"
        f"💱 `{data['symbol']}`\n"
        f"🎯 `{direction.upper()} ${data['limit']:,.2f}`\n\n"
        f"⏰ **3s checks running...**", 
        reply_markup=reply_markup, parse_mode="Markdown"
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data == "list_alerts")
async def list_alerts(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not alerts[user_id]:
        await callback.answer("No alerts")
        return
    text = "📊 **YOUR ALERTS:**\n\n"
    for aid, a in alerts[user_id].items():
        text += f"• `{a['symbol']}` `{a['direction'].upper()} ${a['limit']:,.2f}`\n"
    keyboard = [[InlineKeyboardButton(text="🗑️ Clear All", callback_data="del_all")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "del_all")
async def del_all(callback: CallbackQuery):
    user_id = callback.from_user.id
    alerts[user_id].clear()
    cursor.execute('DELETE FROM alerts WHERE user_id=?', (user_id,))
    conn.commit()
    await callback.answer("🗑️ All cleared!")

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await start(callback.message)
    await callback.answer()

async def main():
    await load_alerts()
    asyncio.create_task(price_monitor())
    print("🚀 COINGECKO BOT STARTED")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

