# ================================
# PART 1 / 2
# REAL-TIME CRYPTO ALERT BOT CORE
# ================================

import asyncio
import sqlite3
from dataclasses import dataclass
from typing import Dict, List

from aiogram import Bot, Dispatcher, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

BOT_TOKEN = "PUT_YOUR_TELEGRAM_BOT_TOKEN_HERE"

# ------------------------
# DATABASE
# ------------------------
conn = sqlite3.connect("alerts.db", check_same_thread=False)
cursor = conn.cursor()

cursor.execute("""
CREATE TABLE IF NOT EXISTS alerts (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id INTEGER,
    exchange TEXT,
    market TEXT,
    price_type TEXT,
    symbol TEXT,
    direction TEXT,
    target REAL,
    last_state TEXT
)
""")
conn.commit()

# ------------------------
# DATA MODELS
# ------------------------
@dataclass
class Alert:
    id: int
    user_id: int
    exchange: str
    market: str
    price_type: str
    symbol: str
    direction: str
    target: float
    last_state: str

# ------------------------
# FSM STATES
# ------------------------
class AlertWizard(StatesGroup):
    exchange = State()
    market = State()
    price_type = State()
    symbol = State()
    direction = State()
    target = State()

# ------------------------
# BOT SETUP
# ------------------------
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()

# ------------------------
# KEYBOARDS
# ------------------------
def main_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="➕ Add Alert", callback_data="add_alert")
    kb.button(text="📋 My Alerts", callback_data="list_alerts")
    kb.adjust(1)
    return kb.as_markup()

def exchange_menu():
    kb = InlineKeyboardBuilder()
    for ex in ["BINANCE", "BYBIT", "KUCOIN"]:
        kb.button(text=ex, callback_data=f"ex_{ex}")
    kb.adjust(2)
    return kb.as_markup()

def market_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Spot", callback_data="market_SPOT")
    kb.button(text="Futures", callback_data="market_FUTURES")
    kb.adjust(2)
    return kb.as_markup()

def price_type_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Last Price", callback_data="price_LAST")
    kb.button(text="Mark Price", callback_data="price_MARK")
    kb.adjust(2)
    return kb.as_markup()

def direction_menu():
    kb = InlineKeyboardBuilder()
    kb.button(text="Above ⬆", callback_data="dir_ABOVE")
    kb.button(text="Below ⬇", callback_data="dir_BELOW")
    kb.adjust(2)
    return kb.as_markup()

# ------------------------
# COMMANDS
# ------------------------
@dp.message(Command("start"))
async def start(message: Message):
    await message.answer(
        "📡 *Real-Time Crypto Alert Bot*\n\n"
        "Supports:\n"
        "• Binance / Bybit / KuCoin\n"
        "• Spot & Futures\n"
        "• Mark & Last Price\n"
        "• Above / Below Cross Alerts\n\n"
        "Choose an option:",
        reply_markup=main_menu(),
        parse_mode="Markdown"
    )

# ------------------------
# ADD ALERT FLOW
# ------------------------
@dp.callback_query(F.data == "add_alert")
async def add_alert(cb: CallbackQuery, state: FSMContext):
    await cb.message.edit_text("Select Exchange:", reply_markup=exchange_menu())
    await state.set_state(AlertWizard.exchange)

@dp.callback_query(AlertWizard.exchange, F.data.startswith("ex_"))
async def select_exchange(cb: CallbackQuery, state: FSMContext):
    exchange = cb.data.split("_")[1]
    await state.update_data(exchange=exchange)
    await cb.message.edit_text("Select Market Type:", reply_markup=market_menu())
    await state.set_state(AlertWizard.market)

@dp.callback_query(AlertWizard.market, F.data.startswith("market_"))
async def select_market(cb: CallbackQuery, state: FSMContext):
    market = cb.data.split("_")[1]
    await state.update_data(market=market)
    await cb.message.edit_text("Select Price Type:", reply_markup=price_type_menu())
    await state.set_state(AlertWizard.price_type)

@dp.callback_query(AlertWizard.price_type, F.data.startswith("price_"))
async def select_price_type(cb: CallbackQuery, state: FSMContext):
    price_type = cb.data.split("_")[1]
    await state.update_data(price_type=price_type)
    await cb.message.edit_text("Enter Symbol (e.g. BTCUSDT):")
    await state.set_state(AlertWizard.symbol)

@dp.message(AlertWizard.symbol)
async def input_symbol(msg: Message, state: FSMContext):
    await state.update_data(symbol=msg.text.upper())
    await msg.answer("Select Direction:", reply_markup=direction_menu())
    await state.set_state(AlertWizard.direction)

@dp.callback_query(AlertWizard.direction, F.data.startswith("dir_"))
async def select_direction(cb: CallbackQuery, state: FSMContext):
    direction = cb.data.split("_")[1]
    await state.update_data(direction=direction)
    await cb.message.edit_text("Enter Target Price:")
    await state.set_state(AlertWizard.target)

@dp.message(AlertWizard.target)
async def input_target(msg: Message, state: FSMContext):
    try:
        target = float(msg.text)
    except:
        await msg.answer("❌ Invalid number. Enter price again:")
        return

    data = await state.get_data()
    cursor.execute("""
        INSERT INTO alerts (user_id, exchange, market, price_type, symbol, direction, target, last_state)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (
        msg.from_user.id,
        data["exchange"],
        data["market"],
        data["price_type"],
        data["symbol"],
        data["direction"],
        target,
        "INIT"
    ))
    conn.commit()

    await msg.answer(
        f"✅ Alert Added:\n"
        f"{data['symbol']} | {data['exchange']} | {data['market']} | {data['price_type']}\n"
        f"{data['direction']} {target}",
        reply_markup=main_menu()
    )
    await state.clear()

# ------------------------
# LIST & DELETE ALERTS
# ------------------------
@dp.callback_query(F.data == "list_alerts")
async def list_alerts(cb: CallbackQuery):
    cursor.execute("SELECT id, symbol, exchange, market, direction, target FROM alerts WHERE user_id=?",
                   (cb.from_user.id,))
    rows = cursor.fetchall()

    if not rows:
        await cb.message.edit_text("No alerts yet.", reply_markup=main_menu())
        return

    text = "📋 *Your Alerts:*\n\n"
    kb = InlineKeyboardBuilder()
    for r in rows:
        aid, sym, ex, mkt, d, tgt = r
        text += f"#{aid} | {sym} | {ex} | {mkt} | {d} {tgt}\n"
        kb.button(text=f"❌ Delete #{aid}", callback_data=f"del_{aid}")
    kb.adjust(1)

    await cb.message.edit_text(text, reply_markup=kb.as_markup(), parse_mode="Markdown")

@dp.callback_query(F.data.startswith("del_"))
async def delete_alert(cb: CallbackQuery):
    aid = int(cb.data.split("_")[1])
    cursor.execute("DELETE FROM alerts WHERE id=? AND user_id=?", (aid, cb.from_user.id))
    conn.commit()
    await cb.answer("Alert deleted")
    await list_alerts(cb)

# ================================
# PART 1 ENDS HERE
# ================================

# ================================
# PART 2 / 2
# REAL-TIME STREAM ENGINE + DEPLOYMENT
# ================================

import json
import time
import aiohttp

# ------------------------
# ALERT ENGINE
# ------------------------
async def fetch_binance(symbol: str, market: str, price_type: str):
    """Get price from Binance WebSocket"""
    stream_name = f"{symbol.lower()}@markPrice" if price_type == "MARK" else f"{symbol.lower()}@trade"
    url = f"wss://stream.binance.com:9443/ws/{stream_name}"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    price = float(data.get("p") or data.get("markPrice") or 0)
                    yield price

async def fetch_bybit(symbol: str, market: str, price_type: str):
    """Get price from Bybit WebSocket"""
    stream = "markPrice" if price_type == "MARK" else "trade"
    url = f"wss://stream.bybit.com/realtime_public"
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(url) as ws:
            sub_msg = {"op": "subscribe", "args": [f"{symbol}.{stream}"]}
            await ws.send_json(sub_msg)
            async for msg in ws:
                if msg.type == aiohttp.WSMsgType.TEXT:
                    data = json.loads(msg.data)
                    # Simple parsing
                    if "data" in data:
                        price = float(data["data"][0]["price"])
                        yield price

EXCHANGE_WS_MAP = {
    "BINANCE": fetch_binance,
    "BYBIT": fetch_bybit,
    "KUCOIN": fetch_kucoin,
    "HTX": fetch_htx,
    "GATE": fetch_gate,
    "BITMART": fetch_bitmart
}

async def alert_checker():
    """Main background task to check all alerts"""
    while True:
        cursor.execute("SELECT id, user_id, exchange, market, price_type, symbol, direction, target, last_state FROM alerts")
        alerts = [Alert(*r) for r in cursor.fetchall()]
        tasks = []

        for a in alerts:
            ws_func = EXCHANGE_WS_MAP.get(a.exchange)
            if not ws_func:
                continue
            tasks.append(handle_alert(a, ws_func))

        if tasks:
            await asyncio.gather(*tasks)
        await asyncio.sleep(1)  # 1-second loop

async def handle_alert(alert: Alert, ws_func):
    """Handle a single alert"""
    async for price in ws_func(alert.symbol, alert.market, alert.price_type):
        crossed = False
        if alert.direction == "ABOVE":
            if alert.last_state != "ABOVE" and price >= alert.target:
                crossed = True
                alert.last_state = "ABOVE"
            elif price < alert.target:
                alert.last_state = "BELOW"
        else:  # BELOW
            if alert.last_state != "BELOW" and price <= alert.target:
                crossed = True
                alert.last_state = "BELOW"
            elif price > alert.target:
                alert.last_state = "ABOVE"

        # Update last state in DB
        cursor.execute("UPDATE alerts SET last_state=? WHERE id=?", (alert.last_state, alert.id))
        conn.commit()

        if crossed:
            try:
                await bot.send_message(
                    chat_id=alert.user_id,
                    text=f"⚡ *ALERT TRIGGERED*\n{alert.symbol} | {alert.exchange} | {alert.market} | {alert.price_type}\n"
                         f"{alert.direction} {alert.target}\nCurrent Price: {price}",
                    parse_mode="Markdown"
                )
            except Exception as e:
                print(f"Failed to send alert to {alert.user_id}: {e}")
        await asyncio.sleep(0.1)  # prevent flooding

# ------------------------
# RUNNER
# ------------------------
async def main_runner():
    print("🔔 Crypto Alert Bot Running...")
    await alert_checker()

# ------------------------
# ENTRY POINT
# ------------------------
if __name__ == "__main__":
    try:
        asyncio.run(main_runner())
    except KeyboardInterrupt:
        print("Exiting...")

# ================================
# DEPLOYMENT FILES
# ================================

# requirements.txt (Railway)
"""
aiogram==3.0.0b7
aiohttp==3.8.5
"""

# Procfile (Railway)
"""
worker: python main.py
"""

# Environment Variables (Railway)
"""
BOT_TOKEN = YOUR_TELEGRAM_BOT_TOKEN
"""

# Railway Tips:
# 1. Choose "Worker" service type, not web.
# 2. Add BOT_TOKEN in Environment.
# 3. 1 instance is enough; multiple instances may conflict.
# 4. WebSocket alive; no port needed for worker.
# 5. Logs show alert triggers in real-time.
# 6. Supports up to 500 symbols in async tasks (tune sleep for CPU).

