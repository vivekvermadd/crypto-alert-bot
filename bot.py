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
from collections import defaultdict
import sqlite3
import json

logging.basicConfig(level=logging.INFO)
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

EXCHANGES = ['binance', 'bybit', 'htx', 'kucoin', 'gateio', 'bitmart']

conn = sqlite3.connect('alerts.db', check_same_thread=False)
cursor = conn.cursor()
cursor.execute('''CREATE TABLE IF NOT EXISTS alerts 
                  (user_id INTEGER, alert_id TEXT PRIMARY KEY, data TEXT, muted BOOLEAN DEFAULT 0)''')
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
        uid, aid, data_json, muted = row
        alert_data = json.loads(data_json)
        alert_data['muted'] = bool(muted)
        alerts[uid][aid] = alert_data

async def save_alert(user_id, alert_id, alert):
    cursor.execute('INSERT OR REPLACE INTO alerts VALUES (?, ?, ?, ?)', 
                   (user_id, alert_id, json.dumps(alert), alert.get('muted', False)))
    conn.commit()

async def get_price(exchange, symbol):
    """ALL 6 EXCHANGES - Working endpoints"""
    try:
        async with aiohttp.ClientSession() as session:
            # BINANCE
            if exchange == 'binance':
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.replace('/','')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return float(data['price'])
            
            # BYBIT
            elif exchange == 'bybit':
                url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol.replace('/','')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('retCode') == 0 and data['result']['list']:
                            return float(data['result']['list'][0]['lastPrice'])
            
            # HTX (Huobi)
            elif exchange == 'htx':
                url = f"https://api.huobi.pro/market/detail/merged?symbol={symbol.lower().replace('/','')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'tick' in data:
                            return float(data['tick']['close'])
            
            # KUCOIN
            elif exchange == 'kucoin':
                url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol.replace('/','-')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('code') == '200000':
                            return float(data['data']['price'])
            
            # GATEIO
            elif exchange == 'gateio':
                url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol.replace('/','_')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for ticker in data:
                            if ticker['currency_pair'] == symbol.replace('/','_'):
                                return float(ticker['last'])
            
            # BITMART
            elif exchange == 'bitmart':
                url = f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol.replace('/','_')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('code') == '1000' and data['data']['tickers']:
                            return float(data['data']['tickers'][0]['last_price'])
    except Exception as e:
        logging.error(f"❌ {exchange} error: {e}")
        return None

async def price_monitor():
    while True:
        print(f"🔄 Checking {sum(len(a) for a in alerts.values())} alerts")
        for user_id, user_alerts in list(alerts.items()):
            for alert_id, alert in list(user_alerts.items()):
                if alert.get('muted', False):
                    continue
                
                price = await get_price(alert['exchange'], alert['symbol'])
                print(f"📊 {alert['exchange'].upper()} {alert['symbol']}: ${price} vs {alert['limit']}")
                
                if price:
                    direction = alert['direction']
                    limit = alert['limit']
                    if (direction == 'above' and price >= limit) or (direction == 'below' and price <= limit):
                        await bot.send_message(
                            user_id,
                            f"🚨 **ALERT ACTIVE!**\n\n"
                            f"📊 `{alert['exchange'].upper()}`\n"
                            f"💱 `{alert['symbol']}`\n"
                            f"💰 **${price:,.2f}**\n"
                            f"🎯 **{direction.upper()} ${limit:,.2f}**\n\n"
                            f"👆 *Click STOP to silence notifications*",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                                [InlineKeyboardButton(text="🛑 STOP ALERT", callback_data=f"stop_{alert_id}")]
                            ])
                        )
                        print(f"🚨 Alert sent for {alert_id}")
        await asyncio.sleep(5)

@dp.message(Command('start'))
async def start(message: types.Message):
    keyboard = [
        [InlineKeyboardButton(text="➕ Set Alert", callback_data="set_alert")],
        [InlineKeyboardButton(text="🧪 Test Prices", callback_data="test_price")],
        [InlineKeyboardButton(text="📋 My Alerts", callback_data="list_alerts")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("🚀 **Crypto Alert Bot**\n\n"
                       "✅ All 6 exchanges\n"
                       "🔔 Persistent alerts until STOP\n"
                       "⏰ 5s live checks\n\n"
                       f"💱 Use: `BTC/USDT`", 
                       reply_markup=reply_markup, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "test_price")
async def test_price(callback: CallbackQuery):
    text = "🧪 **LIVE PRICES** (BTC/USDT):\n\n"
    for ex in EXCHANGES:
        price = await get_price(ex, 'BTC/USDT')
        status = f"`{ex.upper()}`: **${price:,.2f}**" if price else f"`{ex.upper()}`: ❌"
        text += status + "\n"
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "set_alert")
async def set_alert_start(callback: CallbackQuery, state: FSMContext):
    keyboard = [
        [InlineKeyboardButton(text="BINANCE", callback_data="ex_binance"), InlineKeyboardButton(text="BYBIT", callback_data="ex_bybit")],
        [InlineKeyboardButton(text="HTX", callback_data="ex_htx"), InlineKeyboardButton(text="KUCOIN", callback_data="ex_kucoin")],
        [InlineKeyboardButton(text="GATEIO", callback_data="ex_gateio"), InlineKeyboardButton(text="BITMART", callback_data="ex_bitmart")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text("📈 **Select Exchange:**", reply_markup=reply_markup, parse_mode="Markdown")
    await state.set_state(AlertForm.exchange)
    await callback.answer()

@dp.callback_query(AlertForm.exchange)
async def set_exchange(callback: CallbackQuery, state: FSMContext):
    ex = callback.data.split('_')[1]
    await state.update_data(exchange=ex)
    await callback.message.edit_text(
        f"✅ **{ex.upper()} selected**\n\n"
        f"💱 **Enter symbol:**\n"
        f"`BTC/USDT`", parse_mode="Markdown")
    await state.set_state(AlertForm.symbol)
    await callback.answer()

@dp.message(AlertForm.symbol)
async def set_symbol(message: types.Message, state: FSMContext):
    symbol = message.text.strip().upper()
    await state.update_data(symbol=symbol)
    await message.reply("💰 **Enter limit price:**\n\n`90000`", parse_mode="Markdown")
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
        await message.reply("🎯 **Select direction:**", reply_markup=reply_markup, parse_mode="Markdown")
        await state.set_state(AlertForm.direction)
    except:
        await message.reply("❌ **Invalid price.** Enter: `90000`", parse_mode="Markdown")

@dp.callback_query(AlertForm.direction)
async def set_dir(callback: CallbackQuery, state: FSMContext):
    direction = 'above' if 'above' in callback.data else 'below'
    data = await state.get_data()
    user_id = callback.from_user.id
    alert_id = f"{data['exchange']}_{data['symbol']}_{direction}_{int(data['limit'])}"
    alert = {
        'exchange': data['exchange'], 
        'symbol': data['symbol'], 
        'limit': data['limit'], 
        'direction': direction,
        'muted': False
    }
    alerts[user_id][alert_id] = alert
    await save_alert(user_id, alert_id, alert)
    
    keyboard = [[InlineKeyboardButton(text="📋 My Alerts", callback_data="list_alerts")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(
        f"✅ **ALERT CREATED!**\n\n"
        f"📊 `{data['exchange'].upper()}`\n"
        f"💱 `{data['symbol']}`\n"
        f"🎯 `{direction.upper()} ${data['limit']:,.2f}`\n\n"
        f"🔔 *Will alert every 5s until STOP*\n"
        f"⏰ *Background monitoring active*", 
        reply_markup=reply_markup, parse_mode="Markdown"
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("stop_"))
async def stop_alert(callback: CallbackQuery):
    alert_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    if alert_id in alerts[user_id]:
        alerts[user_id][alert_id]['muted'] = True
        await save_alert(user_id, alert_id, alerts[user_id][alert_id])
        await callback.message.edit_text(
            f"🛑 **ALERT MUTED**\n\n"
            f"📊 `{alerts[user_id][alert_id]['exchange'].upper()}`\n"
            f"💱 `{alerts[user_id][alert_id]['symbol']}`\n"
            f"🎯 `{alerts[user_id][alert_id]['direction'].upper()} ${alerts[user_id][alert_id]['limit']:,.2f}`\n\n"
            f"✅ *Background monitoring continues*\n"
            f"👆 *Click RESUME for notifications*",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🔄 RESUME", callback_data=f"resume_{alert_id}")]
            ]),
            parse_mode="Markdown"
        )
    await callback.answer("🛑 Notifications stopped!")

@dp.callback_query(lambda c: c.data.startswith("resume_"))
async def resume_alert(callback: CallbackQuery):
    alert_id = callback.data.split("_")[1]
    user_id = callback.from_user.id
    
    if alert_id in alerts[user_id]:
        alerts[user_id][alert_id]['muted'] = False
        await save_alert(user_id, alert_id, alerts[user_id][alert_id])
        await callback.message.edit_text(
            f"🔄 **ALERT RESUMED**\n\n"
            f"📊 `{alerts[user_id][alert_id]['exchange'].upper()}`\n"
            f"💱 `{alerts[user_id][alert_id]['symbol']}`\n"
            f"🎯 `{alerts[user_id][alert_id]['direction'].upper()} ${alerts[user_id][alert_id]['limit']:,.2f}`\n\n"
            f"🚨 *Notifications restarted*",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🛑 STOP", callback_data=f"stop_{alert_id}")]
            ]),
            parse_mode="Markdown"
        )
    await callback.answer("🔄 Notifications active!")

@dp.callback_query(lambda c: c.data == "list_alerts")
async def list_alerts(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not alerts[user_id]:
        await callback.answer("No alerts active")
        return
    text = "📊 **YOUR ALERTS:**\n\n"
    for aid, a in alerts[user_id].items():
        status = "🔇 MUTED" if a.get('muted', False) else "🔔 ACTIVE"
        text += f"• `{a['exchange'].upper()}` `{a['symbol']}` `{a['direction'].upper()} ${a['limit']:,.2f}` `{status}`\n"
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
    await callback.answer("🗑️ All alerts deleted!")

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await start(callback.message)
    await callback.answer()

async def main():
    await load_alerts()
    asyncio.create_task(price_monitor())
    print("🚀 ALL 6 EXCHANGES + PERSISTENT ALERT BOT STARTED")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
