import asyncio
import logging
import ccxt.async_support as ccxt
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
import time

logging.basicConfig(level=logging.INFO)
logging.getLogger('aiogram.event').setLevel(logging.WARNING)  # Clean logs
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN')
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())

EXCHANGES = ['binance', 'bybit', 'htx', 'kucoin', 'gate', 'bitmart']

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

def get_exchange_obj(ex_id):
    return {
        'binance': ccxt.binance(), 
        'bybit': ccxt.bybit(), 
        'htx': ccxt.htx(),
        'kucoin': ccxt.kucoin(), 
        'gate': ccxt.gate(), 
        'bitmart': ccxt.bitmart()
    }[ex_id]

# RELIABLE 1-SECOND POLLING (FIXES WS ISSUE)
async def price_monitor():
    while True:
        for user_id, user_alerts in list(alerts.items()):
            for alert_id, alert in list(user_alerts.items()):
                try:
                    exchange = get_exchange_obj(alert['exchange'])
                    ticker = await exchange.fetch_ticker(alert['symbol'])
                    price = ticker['last']
                    direction = alert['direction']
                    limit = alert['limit']
                    
                    # TRIGGER ALERT
                    if (direction == 'above' and price >= limit) or (direction == 'below' and price <= limit):
                        await bot.send_message(
                            user_id, 
                            f"🚨 ALERT HIT!\n{alert['exchange'].upper()}\n{alert['symbol']}\n💰 {price:.4f}\n{'📈' if direction=='above' else '📉'} {limit}"
                        )
                        # Delete one-time alert
                        del alerts[user_id][alert_id]
                        cursor.execute('DELETE FROM alerts WHERE user_id=? AND alert_id=?', (user_id, alert_id))
                        conn.commit()
                    
                    await exchange.close()
                except Exception as e:
                    logging.error(f"Price check error {alert_id}: {e}")
        
        await asyncio.sleep(1)  # 1 SECOND CHECKS

@dp.message(Command('start'))
async def start(message: types.Message):
    keyboard = [
        [InlineKeyboardButton(text="➕ Set Alert", callback_data="set_alert")],
        [InlineKeyboardButton(text="📋 List Alerts", callback_data="list_alerts")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("🚀 Trading Alert Bot - 1s checks\nClick ➕ Set Alert:", reply_markup=reply_markup)

# SAME BUTTON HANDLERS (unchanged - working perfectly)
@dp.callback_query(lambda c: c.data == "set_alert")
async def set_alert_start(callback: CallbackQuery, state: FSMContext):
    keyboard = [
        [InlineKeyboardButton(text=ex.upper(), callback_data=f"ex_{ex}") for ex in EXCHANGES[:3]],
        [InlineKeyboardButton(text=ex.upper(), callback_data=f"ex_{ex}") for ex in EXCHANGES[3:]],
        [InlineKeyboardButton(text="❌ Cancel", callback_data="cancel")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text("Select Exchange:", reply_markup=reply_markup)
    await state.set_state(AlertForm.exchange)
    await callback.answer()

@dp.callback_query(AlertForm.exchange)
async def set_exchange(callback: CallbackQuery, state: FSMContext):
    ex = callback.data.split('_')[1]
    await state.update_data(exchange=ex)
    await callback.message.edit_text(f"✅ {ex.upper()}\n\nEnter symbol:\n`BTC/USDT` `ETH/USDT`", parse_mode="Markdown")
    await state.set_state(AlertForm.symbol)
    await callback.answer()

@dp.message(AlertForm.symbol)
async def set_symbol(message: types.Message, state: FSMContext):
    await state.update_data(symbol=message.text.strip().upper())
    await message.reply("Enter limit price:\n`60000`", parse_mode="Markdown")
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
        await message.reply("Select direction:", reply_markup=reply_markup)
        await state.set_state(AlertForm.direction)
    except:
        await message.reply("❌ Invalid number. Enter price like: `60000`")

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
        f"✅ Alert ACTIVE!\n{data['exchange'].upper()}\n{data['symbol']}\n{direction.upper()} {data['limit']}\n\n💡 1s checks running...",
        reply_markup=reply_markup
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data == "list_alerts")
async def list_alerts(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not alerts[user_id]:
        await callback.answer("No active alerts.")
        return
    text = "📊 ACTIVE ALERTS:\n\n"
    for aid, a in alerts[user_id].items():
        text += f"• {a['exchange'].upper()} {a['symbol']} {a['direction'].upper()} {a['limit']}\n"
    keyboard = [[InlineKeyboardButton(text="🗑️ Delete All", callback_data="del_all")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(text, reply_markup=reply_markup)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def del_alert(callback: CallbackQuery):
    user_id = callback.from_user.id
    if callback.data == "del_all":
        alerts[user_id].clear()
        cursor.execute('DELETE FROM alerts WHERE user_id=?', (user_id,))
        conn.commit()
        await callback.answer("🗑️ All alerts deleted!")
    await callback.answer("Deleted!")

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await start(callback.message)
    await callback.answer()

async def main():
    await load_alerts()
    # Start 1s price monitoring
    asyncio.create_task(price_monitor())
    print("🚀 Bot + 1s Price Monitor STARTED")
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())
