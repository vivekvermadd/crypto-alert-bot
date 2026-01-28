import asyncio
import aiohttp
import logging
import json
from aiogram import Bot, Dispatcher, types
from aiogram.filters import Command
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
import os
from collections import defaultdict
import sqlite3

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

class EditForm(StatesGroup):
    new_limit = State()
    edit_alert_id = State()

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
    try:
        async with aiohttp.ClientSession() as session:
            if exchange == 'binance':
                url = f"https://api.binance.com/api/v3/ticker/price?symbol={symbol.replace('/','')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        return float(data['price'])
            elif exchange == 'bybit':
                url = f"https://api.bybit.com/v5/market/tickers?category=spot&symbol={symbol.replace('/','')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('retCode') == 0 and data['result']['list']:
                            return float(data['result']['list'][0]['lastPrice'])
            elif exchange == 'htx':
                url = f"https://api.huobi.pro/market/detail/merged?symbol={symbol.lower().replace('/','')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if 'tick' in data:
                            return float(data['tick']['close'])
            elif exchange == 'kucoin':
                url = f"https://api.kucoin.com/api/v1/market/orderbook/level1?symbol={symbol.replace('/','-')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('code') == '200000':
                            return float(data['data']['price'])
            elif exchange == 'gateio':
                url = f"https://api.gateio.ws/api/v4/spot/tickers?currency_pair={symbol.replace('/','_')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        for ticker in data:
                            if ticker['currency_pair'] == symbol.replace('/','_'):
                                return float(ticker['last'])
            elif exchange == 'bitmart':
                url = f"https://api-cloud.bitmart.com/spot/v1/ticker?symbol={symbol.replace('/','_')}"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        if data.get('code') == '1000' and data['data']['tickers']:
                            return float(data['data']['tickers'][0]['last_price'])
    except:
        return None

async def price_monitor():
    while True:
        for user_id, user_alerts in list(alerts.items()):
            for alert_id, alert in list(user_alerts.items()):
                if alert.get('muted', False):
                    continue
                price = await get_price(alert['exchange'], alert['symbol'])
                if price:
                    direction = alert['direction']
                    limit = alert['limit']
                    if (direction == 'above' and price >= limit) or (direction == 'below' and price <= limit):
                        # ALERT MESSAGE WITH STOP BUTTON
                        keyboard = [
                            [InlineKeyboardButton(text="🛑 STOP THIS ALERT", callback_data=f"stop_{alert_id}")],
                            [InlineKeyboardButton(text="✏️ EDIT PRICE", callback_data=f"edit_{alert_id}")],
                            [InlineKeyboardButton(text="🗑️ DELETE", callback_data=f"delete_{alert_id}")]
                        ]
                        await bot.send_message(
                            user_id,
                            f"🚨 **ALERT TRIGGERED!**\n\n"
                            f"📊 `{alert['exchange'].upper()}`\n"
                            f"💱 `{alert['symbol']}`\n"
                            f"💰 **${price:,.2f}**\n"
                            f"🎯 **{direction.upper()} ${limit:,.2f}**",
                            parse_mode="Markdown",
                            reply_markup=InlineKeyboardMarkup(inline_keyboard=keyboard)
                        )
        await asyncio.sleep(5)

@dp.message(Command('start'))
async def start(message: types.Message):
    keyboard = [
        [InlineKeyboardButton(text="➕ New Alert", callback_data="set_alert")],
        [InlineKeyboardButton(text="🧪 Test Prices", callback_data="test_price")],
        [InlineKeyboardButton(text="📋 Manage Alerts", callback_data="manage_alerts")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("🚀 **ULTIMATE Crypto Alert Bot**\n\n"
                       "✅ 6 Exchanges\n"
                       "🛑 Individual STOP per alert\n"
                       "✏️ Edit price per alert\n"
                       "🗑️ Delete individual\n"
                       "⏰ 5s monitoring", 
                       reply_markup=reply_markup, parse_mode="Markdown")

@dp.callback_query(lambda c: c.data == "test_price")
async def test_price(callback: CallbackQuery):
    text = "🧪 **LIVE PRICES (BTC/USDT):**\n\n"
    for ex in EXCHANGES:
        price = await get_price(ex, 'BTC/USDT')
        status = f"`{ex.upper()}`: **${price:,.2f}**" if price else f"`{ex.upper()}`: ❌"
        text += status + "\n"
    await callback.message.edit_text(text, parse_mode="Markdown")
    await callback.answer()

@dp.callback_query(lambda c: c.data == "set_alert")
async def set_alert_start(callback: CallbackQuery, state: FSMContext):
    keyboard = [
        [InlineKeyboardButton(text="BINANCE", callback_data="ex_binance"), 
         InlineKeyboardButton(text="BYBIT", callback_data="ex_bybit")],
        [InlineKeyboardButton(text="HTX", callback_data="ex_htx"), 
         InlineKeyboardButton(text="KUCOIN", callback_data="ex_kucoin")],
        [InlineKeyboardButton(text="GATEIO", callback_data="ex_gateio"), 
         InlineKeyboardButton(text="BITMART", callback_data="ex_bitmart")],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="start_menu")]
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
    await message.reply("💰 **Enter limit price:**\n`90000`", parse_mode="Markdown")
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
        await message.reply("❌ **Enter number:** `90000`", parse_mode="Markdown")

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
    
    await callback.message.edit_text(
        f"✅ **ALERT CREATED!**\n\n"
        f"📊 `{data['exchange'].upper()}`\n"
        f"💱 `{data['symbol']}`\n"
        f"🎯 `{direction.upper()} ${data['limit']:,.2f}`\n\n"
        f"📋 Click **Manage Alerts** for controls",
        parse_mode="Markdown"
    )
    await state.clear()
    await callback.answer()

# FIXED: MANAGE ALERTS WITH INDIVIDUAL BUTTONS
@dp.callback_query(lambda c: c.data == "manage_alerts")
async def manage_alerts(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not alerts[user_id]:
        await callback.message.edit_text("📭 **No alerts.** Create one first!")
        await callback.answer()
        return
    
    text = "📋 **MANAGE YOUR ALERTS:**\n\n"
    keyboard = []
    for aid, alert in alerts[user_id].items():
        status = "🔇 MUTED" if alert.get('muted', False) else "🔔 ACTIVE"
        text += f"• `{alert['exchange'].upper()}` `{alert['symbol']}` `{alert['direction'].upper()} ${alert['limit']:,.2f}` `{status}`\n"
        keyboard.extend([
            [
                InlineKeyboardButton(text=f"🛑 STOP {alert['symbol'][:8]}", callback_data=f"stop_{aid}"),
                InlineKeyboardButton(text=f"🔄 {alert['symbol'][:8]}", callback_data=f"resume_{aid}")
            ],
            [
                InlineKeyboardButton(text=f"✏️ EDIT {alert['symbol'][:8]}", callback_data=f"edit_{aid}"),
                InlineKeyboardButton(text=f"🗑️ DEL {alert['symbol'][:8]}", callback_data=f"delete_{aid}")
            ]
        ])
    keyboard.append([InlineKeyboardButton(text="🔙 Main Menu", callback_data="start_menu")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(text, reply_markup=reply_markup, parse_mode="Markdown")
    await callback.answer()

# FIXED: ALL INDIVIDUAL BUTTON HANDLERS
@dp.callback_query(lambda c: c.data.startswith("stop_"))
async def stop_alert(callback: CallbackQuery):
    parts = callback.data.split("_", 1)
    alert_id = parts[1]
    user_id = callback.from_user.id
    
    if alert_id in alerts[user_id]:
        alerts[user_id][alert_id]['muted'] = True
        await save_alert(user_id, alert_id, alerts[user_id][alert_id])
        await callback.answer(f"🛑 Alert **{alert_id}** stopped!", show_alert=True)
    else:
        await callback.answer("❌ Alert not found!")

@dp.callback_query(lambda c: c.data.startswith("resume_"))
async def resume_alert(callback: CallbackQuery):
    parts = callback.data.split("_", 1)
    alert_id = parts[1]
    user_id = callback.from_user.id
    
    if alert_id in alerts[user_id]:
        alerts[user_id][alert_id]['muted'] = False
        await save_alert(user_id, alert_id, alerts[user_id][alert_id])
        await callback.answer(f"🔄 Alert **{alert_id}** resumed!", show_alert=True)
    else:
        await callback.answer("❌ Alert not found!")

@dp.callback_query(lambda c: c.data.startswith("edit_"))
async def edit_alert_start(callback: CallbackQuery, state: FSMContext):
    parts = callback.data.split("_", 1)
    alert_id = parts[1]
    user_id = callback.from_user.id
    
    if alert_id in alerts[user_id]:
        alert = alerts[user_id][alert_id]
        await state.update_data(alert_id=alert_id)
        text = f"✏️ **EDIT PRICE**\n\n"
        text += f"📊 `{alert['exchange'].upper()}` `{alert['symbol']}`\n"
        text += f"🎯 Current: `{alert['direction'].upper()} ${alert['limit']:,.2f}`\n\n"
        text += f"💰 **Enter NEW PRICE:**"
        await callback.message.edit_text(text, parse_mode="Markdown")
        await state.set_state(EditForm.new_limit)
        await callback.answer()
    else:
        await callback.answer("❌ Alert not found!")

@dp.message(EditForm.new_limit)
async def edit_limit(message: types.Message, state: FSMContext):
    try:
        new_limit = float(message.text)
        data = await state.get_data()
        alert_id = data['alert_id']
        user_id = message.from_user.id
        
        if alert_id in alerts[user_id]:
            alerts[user_id][alert_id]['limit'] = new_limit
            await save_alert(user_id, alert_id, alerts[user_id][alert_id])
            alert = alerts[user_id][alert_id]
            await message.reply(
                f"✅ **PRICE UPDATED!**\n\n"
                f"📊 `{alert['exchange'].upper()}`\n"
                f"💱 `{alert['symbol']}`\n"
                f"🎯 `{alert['direction'].upper()} **${new_limit:,.2f}**`\n\n"
                f"📋 `Manage Alerts`",
                parse_mode="Markdown"
            )
        await state.clear()
    except:
        await message.reply("❌ Enter valid number: `90000`")

@dp.callback_query(lambda c: c.data.startswith("delete_"))
async def delete_alert(callback: CallbackQuery):
    parts = callback.data.split("_", 1)
    alert_id = parts[1]
    user_id = callback.from_user.id
    
    if alert_id in alerts[user_id]:
        del alerts[user_id][alert_id]
        cursor.execute('DELETE FROM alerts WHERE user_id=? AND alert_id=?', (user_id, alert_id))
        conn.commit()
        await callback.answer(f"🗑️ Alert **{alert_id}** deleted!", show_alert=True)
        await manage_alerts(callback)  # Refresh list
    else:
        await callback.answer("❌ Alert not found!")

@dp.callback_query(lambda c: c.data == "start_menu")
async def start_menu(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await start(callback.message)
    await callback.answer()

async def main():
    await load_alerts()
    asyncio.create_task(price_monitor())
    print("🚀 ULTIMATE BOT STARTED - All buttons fixed!")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
