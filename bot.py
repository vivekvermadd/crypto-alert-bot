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

logging.basicConfig(level=logging.INFO)
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
tasks = defaultdict(list)

logging.getLogger('aiogram.event').setLevel(logging.WARNING)


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

@dp.message(Command('start'))
async def start(message: types.Message):
    keyboard = [
        [InlineKeyboardButton(text="➕ Set Alert", callback_data="set_alert")],
        [InlineKeyboardButton(text="📋 List Alerts", callback_data="list_alerts")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("Crypto Alert Bot (1s WS checks)\nUse buttons:", reply_markup=reply_markup)

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
    await callback.message.edit_text(f"Exchange: {ex.upper()}\nEnter symbol (e.g. BTC/USDT):")
    await state.set_state(AlertForm.symbol)
    await callback.answer()

@dp.message(AlertForm.symbol)
async def set_symbol(message: types.Message, state: FSMContext):
    await state.update_data(symbol=message.text.strip().upper())
    await message.reply("Enter limit price:")
    await state.set_state(AlertForm.limit)

@dp.message(AlertForm.limit)
async def set_limit(message: types.Message, state: FSMContext):
    limit = float(message.text)
    await state.update_data(limit=limit)
    keyboard = [
        [InlineKeyboardButton(text="📈 Above", callback_data="dir_above")],
        [InlineKeyboardButton(text="📉 Below", callback_data="dir_below")]
    ]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await message.reply("Direction:", reply_markup=reply_markup)
    await state.set_state(AlertForm.direction)

@dp.callback_query(AlertForm.direction)
async def set_dir(callback: CallbackQuery, state: FSMContext):
    direction = 'above' if 'above' in callback.data else 'below'
    data = await state.get_data()
    user_id = callback.from_user.id
    alert_id = f"{data['exchange']}_{data['symbol']}_{direction}_{data['limit']}"
    alert = {
        'exchange': data['exchange'], 
        'symbol': data['symbol'], 
        'limit': data['limit'], 
        'direction': direction
    }
    alerts[user_id][alert_id] = alert
    await save_alert(user_id, alert_id, alert)
    await start_ws_monitor(user_id, alert_id, alert)
    keyboard = [[InlineKeyboardButton(text="📋 List", callback_data="list_alerts")]]
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(
        f"✅ Alert set!\n{data['exchange'].upper()} {data['symbol']} {direction} {data['limit']}", 
        reply_markup=reply_markup
    )
    await state.clear()
    await callback.answer()

@dp.callback_query(lambda c: c.data == "list_alerts")
async def list_alerts(callback: CallbackQuery):
    user_id = callback.from_user.id
    if not alerts[user_id]:
        await callback.answer("No alerts.")
        return
    text = "Alerts:\n" + "\n".join(
        f"{aid}: {a['exchange'].upper()} {a['symbol']} {a['direction']} {a['limit']}" 
        for aid, a in alerts[user_id].items()
    )
    keyboard = [[InlineKeyboardButton(text="🗑️ Del All", callback_data="del_all")]]
    for aid in alerts[user_id]:
        keyboard.append([InlineKeyboardButton(text=f"🗑️ {aid[:20]}...", callback_data=f"del_{aid}")])
    reply_markup = InlineKeyboardMarkup(inline_keyboard=keyboard)
    await callback.message.edit_text(text, reply_markup=reply_markup)
    await callback.answer()

@dp.callback_query(lambda c: c.data.startswith("del_"))
async def del_alert(callback: CallbackQuery):
    user_id = callback.from_user.id
    if callback.data == "del_all":
        for aid in list(alerts[user_id]):
            await stop_ws_monitor(user_id, aid)
            del alerts[user_id][aid]
            cursor.execute('DELETE FROM alerts WHERE user_id=? AND alert_id=?', (user_id, aid))
        conn.commit()
        await callback.answer("All deleted.")
    else:
        aid = callback.data[4:]
        await stop_ws_monitor(user_id, aid)
        del alerts[user_id][aid]
        cursor.execute('DELETE FROM alerts WHERE user_id=? AND alert_id=?', (user_id, aid))
        conn.commit()
        await callback.answer("Deleted.")
    await list_alerts(callback)

async def start_ws_monitor(user_id, alert_id, alert):
    async def monitor():
        exchange = get_exchange_obj(alert['exchange'])
        try:
            while alert_id in alerts[user_id]:
                ticker = await exchange.watch_ticker(alert['symbol'])
                price = ticker['last']
                direction = alert['direction']
                if (direction == 'above' and price >= alert['limit']) or \
                   (direction == 'below' and price <= alert['limit']):
                    try:
                        await bot.send_message(
                            user_id, 
                            f"🚨 ALERT: {alert['exchange'].upper()} {alert['symbol']} {price:.4f} "
                            f"({direction} {alert['limit']})"
                        )
                    except:
                        pass
                    await stop_ws_monitor(user_id, alert_id)
                    break
        except Exception as e:
            logging.error(f"WS error {alert_id}: {e}")
        finally:
            await exchange.close()
    task = asyncio.create_task(monitor())
    tasks[user_id].append(task)

async def stop_ws_monitor(user_id, alert_id):
    for task in tasks[user_id][:]:
        if not task.done():
            task.cancel()
    tasks[user_id] = [t for t in tasks[user_id] if not t.done()]

@dp.callback_query(lambda c: c.data == "cancel")
async def cancel(callback: CallbackQuery, state: FSMContext):
    await state.clear()
    await start(callback.message)
    await callback.answer()

async def main():
    await load_alerts()
    for uid, ualerts in alerts.items():
        for aid, alrt in ualerts.items():
            await start_ws_monitor(uid, aid, alrt)
    await dp.start_polling(bot)

if __name__ == '__main__':
    asyncio.run(main())

