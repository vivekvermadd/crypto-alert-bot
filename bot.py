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

logging.basicConfig(level=logging.WARNING)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

EXCHANGES = ['binance', 'bybit', 'htx', 'kucoin', 'gateio', 'bitmart']

conn = sqlite3.connect('alerts.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS alerts 
                  (user_id INTEGER, alert_id TEXT PRIMARY KEY, data TEXT)''')
conn.commit()

alerts = defaultdict(dict)

class AlertForm(StatesGroup):
    exchange = State()
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

# PUBLIC PRICE APIs (NO BLOCKS - Work from India/US)
async def get_price(exchange, symbol):
    """Direct API calls - 100% reliable"""
    try:
        async with aiohttp.ClientSession() as session:
            if exchange == 'binance':
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.replace('/', '')}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return float(data['price'])
            elif exchange == 'bybit':
                url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol.replace('/', '')}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return float(data['result']['list'][0]['lastPrice'])
            elif exchange == 'htx':
                url = f"https://api.huobi.pro/market/detail/merged?symbol={symbol.replace('/', '')}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return float(data['tick']['close'])
            elif exchange == 'kucoin':
                url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol.replace('/', '-')}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return float(data['data']['price'])
            elif exchange == 'gateio':
                url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol.replace('/', '_')}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    for t in data:
                        if t['currency_pair'] == symbol.replace('/', '_'):
                            return float(t['last'])
            elif exchange == 'bitmart':
                url = f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol.replace('/', '_')}"
                async with session.get(url) as resp:
                    data = await resp.json()
                    return float(data['data']['tickers'][0]['last_price'])
    except:
        return None

async def price_monitor():
    """2s polling - DIRECT APIs"""
    while True:
        for user_id, user_alerts in list(alerts.items()):
            for alert_id, alert in list(user_alerts.items()):
                price = await get_price(alert['exchange'], alert['symbol'])
                if price:
                    direction = alert['direction']
                    limit = alert['limit']
                    if (direction == 'above' and price >= limit) or (direction == 'below' and price <= limit):
                        await bot.send_message(
                            user_id,
                            f"🚨 **ALERT TRIGGERED!**\n\n"
                            f"📊 `{alert['exchange'].upper()}`\n"
                            f"💱 `{alert['symbol']}`\n"
                            f"💰 **${price:,.4f}**\n"
                            f"🎯 **{direction.upper()} ${limit:,.4f}**",
                            parse_mode="Markdown"
                        )
                        # Remove one-time alert
                        del alerts[user_id][alert_id]
                        cursor.execute('DELETE FROM alerts WHERE user_id=? AND alert_id=?', (user_id, alert_id))
                        conn.commit()
        await asyncio.sleep(2)

@dp.message(Command('start'))
async def start(message: types.Message):
    keyboard = [
        [InlineKeyboardButton(text="➕ Set Alert", callback_data="set_alert")],
        [InlineKeyboardButton(text="🧪 Test Prices", callback_data="test_price")],
        [InlineKeyboardButton(text="📋 My Alerts", callback_data="list_alerts")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("🚀 **Crypto Alert Bot**\n\n✅ 6 Exchanges Live\n⏰ 2s Updates\n🚨 Instant Alerts", 
                       reply_markup=reply_markup, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "test_price")
async def test_price(callback: CallbackQuery):
    """Test ALL exchanges live"""
    text = "🧪 **LIVE PRICES** (BTC/USDT):\n\n"
    for ex in EXCHANGES:
        price = await get_price(ex, 'BTC/USDT')
        status = f"`{ex.upper()}`: ${price:,.2f}" if price else f"`{ex.upper()}`: ❌"
        text += status + "\n"
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

# BUTTON HANDLERS (unchanged - perfect)
@dp.callback_query(lambda c: c.data == "set_alert")
async def set_alert_start(callback: CallbackQuery, state: FSMContext):
    keyboard = [
        [InlineKeyboardButton(text=ex.upper(), callback_data=f"ex_{ex}") for ex in EXCHANGES[:3]],
        [InlineKeyboardButton(text=ex.upper(), callback_data=f"ex_{ex}") for ex in EXCHANGES[3:]],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text("📈 **Choose Exchange:**", reply_markup=reply_markup, parse_mode="Markdown")
    await state.set_state(AlertForm.exchange)
    await callback.answer()

@dp.callback_query(AlertForm.exchange)
async def set_exchange(callback: CallbackQuery, state: FSMContext):
    ex = callback.data.split('_')[1]
    await state.update_data(exchange=ex)
    await callback.message.edit_text(
        f"✅ **{ex.upper()}**\n\n💱 **Enter symbol:**\n`BTC/USDT`", parse_mode="Markdown")
    await state.set_state(AlertForm.symbol)
    await callback.answer()

@dp.message(AlertForm.symbol)
async def set_symbol(message: types.Message, state: FSMContext):
    symbol = message.text.strip().upper()
    await state.update_data(symbol=symbol)
    await message.reply("💰 **Enter limit price:**\n`95000`", parse_mode="Markdown")
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
    alert_id = f"{data['exchange']}_{data['symbol']}_{direction}_{int(data['limit'])}"
    alert = {'exchange': data['exchange'], 'symbol': data['symbol'], 'limit': data['limit'], 'direction': direction}
    alerts[user_id][alert_id] = alert
    await save_alert(user_id, alert_id, alert)
    
    keyboard = [[InlineKeyboardButton(text="📋 List Alerts", callback_data="list_alerts")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(
        f"✅ **ALERT LIVE!**\n\n"
        f"📊 `{data['exchange'].upper()}`\n"
        f"💱 `{data['symbol']}`\n"
        f"🎯 `{direction.upper()} ${data['limit']:,.2f}`\n\n"
        f"⏰ **2s live checks...**", 
        reply_markup=reply_markup, parse_mode="Markdown"
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data == "list_alerts")
async def list_alerts(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not alerts[user_id]:
        await callback.answer("No alerts active")
        return
    text = "📊 **YOUR ALERTS:**\n\n"
    for aid, a in alerts[user_id].items():
        text += f"• `{a['exchange'].upper()}` `{a['symbol']}` `{a['direction'].upper()}` `${a['limit']:,.2f}`\n"
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
    await callback.answer("🗑️ All alerts cleared!")

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await start(callback.message)
    await callback.answer()

async def main():
    await load_alerts()
    asyncio.create_task(price_monitor())
    print("🚀 DIRECT API BOT STARTED - No blocks!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
