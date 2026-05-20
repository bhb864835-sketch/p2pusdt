import logging
import asyncio
import aiohttp
import json
import hmac
import hashlib
import time
from datetime import datetime
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = "YOUR_BOT_TOKEN_HERE"

(MAIN_MENU, SETTINGS_MENU, SET_BOT_NAME, SET_FIAT, SET_PAY_METHODS,
 SET_COIN, SET_MAX_AMOUNT, SET_MIN_AMOUNT, SET_TARGET_TYPE,
 SET_TARGET_PRICE, SET_MAX_ORDERS, SET_TAKE_FULL, SET_API_KEY,
 SET_SECRET_KEY, RUNNING) = range(15)

def default_user_data():
    return {
        "bot_name": "My P2P Bot",
        "fiat": "MMK",
        "pay_methods": [],
        "coin": "USDT",
        "max_amount": 15000000,
        "min_amount": 1000,
        "target_type": "price",
        "target_price": 4000,
        "max_orders": 1,
        "take_full_bank": False,
        "api_key": "",
        "secret_key": "",
        "is_running": False,
        "total_orders": 0,
        "monthly_orders": 0,
        "trading_volume": 0,
        "subscription_end": "03.01.2028",
        "bot_id": "BOT" + str(abs(hash(str(datetime.now()))))[:8]
    }

PAY_METHODS_LIST = [
    "WaveMoney", "WaveMobile", "CBPay", "UABPay",
    "SpecificBank", "KBZPay1", "WavePay1", "BANK",
    "AYAPay", "CashDeposit"
]

def get_user_data(context):
    if "data" not in context.user_data:
        context.user_data["data"] = default_user_data()
    return context.user_data["data"]

# ─── BINANCE P2P AUTO-BUY ─────────────────────────────────────────────────────

async def scan_and_buy_p2p(context, user_id, data):
    """Scan P2P orders and auto-buy if conditions met"""
    try:
        # Step 1: Search for P2P ads
        search_url = "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search"
        
        pay_types = []
        if data["pay_methods"]:
            pay_types = data["pay_methods"]
        
        payload = {
            "asset": data["coin"],
            "fiat": data["fiat"],
            "merchantCheck": False,
            "page": 1,
            "payTypes": pay_types,
            "rows": 20,
            "tradeType": "BUY",
            "transAmount": str(data["min_amount"])
        }
        
        headers = {
            "Content-Type": "application/json",
            "User-Agent": "Mozilla/5.0"
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(search_url, json=payload, headers=headers) as resp:
                if resp.status != 200:
                    return
                result = await resp.json()
                ads = result.get("data", [])
                
                for ad in ads:
                    adv = ad.get("adv", {})
                    price = float(adv.get("price", 0))
                    adv_no = adv.get("advNo", "")
                    min_amt = float(adv.get("minSingleTransAmount", 0))
                    max_amt = float(adv.get("maxSingleTransAmount", 0))
                    trade_type = adv.get("tradeType", "")
                    
                    # Check if price meets target
                    if data["target_type"] == "price":
                        price_ok = price <= data["target_price"]
                    else:
                        price_ok = price <= data["target_price"]
                    
                    if not price_ok:
                        continue
                    
                    # Check amount range
                    buy_amount = min(data["max_amount"], max_amt)
                    buy_amount = max(buy_amount, min_amt)
                    
                    if buy_amount < min_amt or buy_amount > max_amt:
                        continue
                    
                    # Step 2: Place order
                    success = await place_p2p_order(
                        session, data, adv_no, buy_amount, price, user_id, context
                    )
                    
                    if success:
                        diff = price - data["target_price"]
                        msg = (
                            f"🟢 SUCCESS\n\n"
                            f"💰 Diff: {diff:.1f}\n"
                            f"📊 Rate: {price:.0f}\n"
                            f"🌍 Fiat: {data['fiat']}\n"
                            f"💵 Taken: {buy_amount:.0f}"
                        )
                        await context.bot.send_message(user_id, msg)
                        data["total_orders"] += 1
                        data["monthly_orders"] += 1
                        data["trading_volume"] += buy_amount
                        break
                        
    except Exception as e:
        logger.error(f"P2P scan error: {e}")

async def place_p2p_order(session, data, adv_no, amount, price, user_id, context):
    """Place a P2P buy order using Binance API"""
    try:
        timestamp = int(time.time() * 1000)
        
        order_payload = {
            "advNo": adv_no,
            "tradeType": "BUY",
            "asset": data["coin"],
            "fiatUnit": data["fiat"],
            "amount": str(amount),
            "price": str(price),
            "timestamp": timestamp
        }
        
        # Create signature
        query_string = json.dumps(order_payload, separators=(',', ':'))
        signature = hmac.new(
            data["secret_key"].encode('utf-8'),
            query_string.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        
        headers = {
            "Content-Type": "application/json",
            "X-MBX-APIKEY": data["api_key"],
            "User-Agent": "Mozilla/5.0"
        }
        
        order_payload["signature"] = signature
        
        order_url = "https://p2p.binance.com/bapi/c2c/v1/private/c2c/order/create"
        
        async with session.post(order_url, json=order_payload, headers=headers) as resp:
            result = await resp.json()
            if result.get("success") or result.get("code") == "000000":
                return True
            else:
                error_msg = result.get("message", "Unknown error")
                logger.error(f"Order failed: {error_msg}")
                await context.bot.send_message(
                    user_id,
                    f"❌ Order Failed: {error_msg}"
                )
                return False
                
    except Exception as e:
        logger.error(f"Place order error: {e}")
        return False

async def run_bot_loop(context, user_id, data):
    """Main bot scanning loop"""
    await context.bot.send_message(user_id, "🚀 Bot Started! Scanning P2P orders...")
    while data.get("is_running"):
        try:
            await scan_and_buy_p2p(context, user_id, data)
        except Exception as e:
            logger.error(f"Bot loop error: {e}")
        await asyncio.sleep(15)  # Scan every 15 seconds

# ─── MAIN MENU ────────────────────────────────────────────────────────────────

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    data = get_user_data(context)
    status = "Active 🟢" if data["is_running"] else "Stopped 🔴"
    text = (
        f"🤖 Welcome to the {data['bot_name']} Menu!\n\n"
        f"Status of your bot: {status}\n\n"
        "🚀 Start Operation — Launch the bot to perform tasks.\n"
        "⚙️ Configure — Modify the bot's settings.\n\n"
        f"BotID: {data['bot_id']}"
    )
    if data["is_running"]:
        keyboard = [
            [InlineKeyboardButton("⏹ Stop Bot", callback_data="stop_bot")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("📋 Statistics", callback_data="statistics")],
        ]
    else:
        keyboard = [
            [InlineKeyboardButton("🚀 Start Bot", callback_data="start_bot")],
            [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
            [InlineKeyboardButton("📋 Statistics", callback_data="statistics")],
        ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    if update.message:
        await update.message.reply_text(text, reply_markup=reply_markup)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=reply_markup)
    return MAIN_MENU

async def start_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    if not data["api_key"] or not data["secret_key"]:
        await query.edit_message_text(
            "⚠️ API Key နှင့် Secret Key မထည့်ရသေးဘူး။",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="main_menu")]])
        )
        return MAIN_MENU
    data["is_running"] = True
    asyncio.create_task(run_bot_loop(context, query.from_user.id, data))
    return await start(update, context)

async def stop_bot(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    data["is_running"] = False
    await context.bot.send_message(query.from_user.id, "⏹ Bot Stopped!")
    return await start(update, context)

async def statistics(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    text = (
        f"Hello there! 👋\n\n"
        f"📅 Subscription End Date: {data['subscription_end']}\n"
        f"📈 Orders in the Last Month: {data['monthly_orders']}\n"
        f"🕐 Total Orders Ever: {data['total_orders']}\n"
        f"🤖 Trading Volume via Bot: {data['trading_volume']}"
    )
    keyboard = [
        [InlineKeyboardButton("🛒 Renew Subscription", callback_data="renew")],
        [InlineKeyboardButton("📋 Order History", callback_data="order_history")],
        [InlineKeyboardButton("◀️ Back", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return MAIN_MENU

async def settings_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    if data["is_running"]:
        await query.answer("🚫 Stop the bot to change the settings", show_alert=True)
        return MAIN_MENU
    text = "⚙️ Settings Menu\nPlease ensure all settings are configured:"
    keyboard = [
        [InlineKeyboardButton(f"Bot name [{data['bot_name']}]", callback_data="set_bot_name")],
        [InlineKeyboardButton(f"Fiat [{data['fiat']}]", callback_data="set_fiat")],
        [InlineKeyboardButton(f"Pay Methods [{len(data['pay_methods'])}]", callback_data="set_pay_methods")],
        [InlineKeyboardButton(f"Coin [{data['coin']}]", callback_data="set_coin")],
        [InlineKeyboardButton(f"Max amount [{data['max_amount']}]", callback_data="set_max_amount")],
        [InlineKeyboardButton(f"Min amount [{data['min_amount']}]", callback_data="set_min_amount")],
        [InlineKeyboardButton(f"Target: [{data['target_type']}]", callback_data="set_target_type")],
        [InlineKeyboardButton(f"Target price/percent [Less {data['target_price']}]", callback_data="set_target_price")],
        [InlineKeyboardButton(f"Max num orders [{data['max_orders']}]", callback_data="set_max_orders")],
        [InlineKeyboardButton(f"Take Full bank orders [{'On' if data['take_full_bank'] else 'Off'}]", callback_data="toggle_take_full")],
        [InlineKeyboardButton("🔑 API Key", callback_data="set_api_key")],
        [InlineKeyboardButton("◀️ Back", callback_data="main_menu")],
    ]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def ask_bot_name(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "✏️ Enter your Bot Name:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]])
    )
    return SET_BOT_NAME

async def save_bot_name(update, context):
    get_user_data(context)["bot_name"] = update.message.text
    await update.message.reply_text("✅ Bot name saved!")
    return await settings_menu_msg(update, context)

async def ask_fiat(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("MMK", callback_data="fiat_MMK"),
         InlineKeyboardButton("USD", callback_data="fiat_USD"),
         InlineKeyboardButton("THB", callback_data="fiat_THB")],
        [InlineKeyboardButton("◀️ Back", callback_data="settings")]
    ]
    await query.edit_message_text("💱 Select Fiat Currency:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SET_FIAT

async def save_fiat(update, context):
    query = update.callback_query
    await query.answer()
    get_user_data(context)["fiat"] = query.data.replace("fiat_", "")
    return await settings_menu(update, context)

async def ask_pay_methods(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    selected = data["pay_methods"]
    keyboard = [[InlineKeyboardButton("Choose All Pay Methods", callback_data="pay_all")]]
    for method in PAY_METHODS_LIST:
        mark = "✅ " if method in selected else ""
        keyboard.append([InlineKeyboardButton(f"{mark}{method}", callback_data=f"pay_{method}")])
    keyboard.append([
        InlineKeyboardButton("◀️ Back", callback_data="settings"),
        InlineKeyboardButton("Next ▶️", callback_data="settings")
    ])
    keyboard.append([InlineKeyboardButton("Cancel", callback_data="settings")])
    await query.edit_message_text(
        "💳 Select Your Bank for Payment:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SET_PAY_METHODS

async def toggle_pay_method(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    if query.data == "pay_all":
        if len(data["pay_methods"]) == len(PAY_METHODS_LIST):
            data["pay_methods"] = []
        else:
            data["pay_methods"] = PAY_METHODS_LIST.copy()
    else:
        method = query.data.replace("pay_", "")
        if method in data["pay_methods"]:
            data["pay_methods"].remove(method)
        else:
            data["pay_methods"].append(method)
    return await ask_pay_methods(update, context)

async def ask_max_amount(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    await query.edit_message_text(
        f"💰 Enter Max Amount:\nCurrent: {data['max_amount']}\nNumbers only",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]])
    )
    return SET_MAX_AMOUNT

async def save_max_amount(update, context):
    try:
        get_user_data(context)["max_amount"] = int(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await settings_menu_msg(update, context)

async def ask_min_amount(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    await query.edit_message_text(
        f"💰 Enter Min Amount:\nCurrent: {data['min_amount']}\nNumbers only",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]])
    )
    return SET_MIN_AMOUNT

async def save_min_amount(update, context):
    try:
        get_user_data(context)["min_amount"] = int(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await settings_menu_msg(update, context)

async def ask_target_price(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    await query.edit_message_text(
        f"🎯 Enter Target Price:\nCurrent: {data['target_price']}\nNumbers only",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]])
    )
    return SET_TARGET_PRICE

async def save_target_price(update, context):
    try:
        get_user_data(context)["target_price"] = float(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await settings_menu_msg(update, context)

async def ask_max_orders(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    await query.edit_message_text(
        f"🔢 Enter Max Orders:\nCurrent: {data['max_orders']}\nNumbers only",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]])
    )
    return SET_MAX_ORDERS

async def save_max_orders(update, context):
    try:
        get_user_data(context)["max_orders"] = int(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await settings_menu_msg(update, context)

async def toggle_take_full(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    data["take_full_bank"] = not data["take_full_bank"]
    return await settings_menu(update, context)

async def ask_api_key(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    status_api = "✅ Set" if data["api_key"] else "❌ Not Set"
    status_secret = "✅ Set" if data["secret_key"] else "❌ Not Set"
    keyboard = [
        [InlineKeyboardButton("+ Add API Key", callback_data="enter_api_key")],
        [InlineKeyboardButton("+ Add Secret Key", callback_data="enter_secret_key")],
        [InlineKeyboardButton("◀️ Back", callback_data="settings")],
    ]
    await query.edit_message_text(
        f"🔑 API Key Menu\n\nAPI Key: {status_api}\nSecret Key: {status_secret}\n\nSelect option:",
        reply_markup=InlineKeyboardMarkup(keyboard)
    )
    return SETTINGS_MENU

async def enter_api_key(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔑 Enter your Binance API Key:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="set_api_key")]])
    )
    return SET_API_KEY

async def enter_secret_key(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🔐 Enter your Binance Secret Key:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="set_api_key")]])
    )
    return SET_SECRET_KEY

async def save_api_key(update, context):
    get_user_data(context)["api_key"] = update.message.text.strip()
    await update.message.delete()
    await update.message.reply_text("✅ API Key saved!")
    return await settings_menu_msg(update, context)

async def save_secret_key(update, context):
    get_user_data(context)["secret_key"] = update.message.text.strip()
    await update.message.delete()
    await update.message.reply_text("✅ Secret Key saved!")
    return await settings_menu_msg(update, context)

async def set_target_type(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("📊 Price", callback_data="target_type_price")],
        [InlineKeyboardButton("📉 Percent", callback_data="target_type_percent")],
        [InlineKeyboardButton("◀️ Back", callback_data="settings")],
    ]
    await query.edit_message_text("🎯 Select Target Type:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SET_TARGET_TYPE

async def save_target_type(update, context):
    query = update.callback_query
    await query.answer()
    get_user_data(context)["target_type"] = query.data.replace("target_type_", "")
    return await settings_menu(update, context)

async def set_coin(update, context):
    query = update.callback_query
    await query.answer()
    keyboard = [
        [InlineKeyboardButton("USDT", callback_data="coin_USDT"),
         InlineKeyboardButton("BTC", callback_data="coin_BTC"),
         InlineKeyboardButton("ETH", callback_data="coin_ETH")],
        [InlineKeyboardButton("◀️ Back", callback_data="settings")]
    ]
    await query.edit_message_text("🪙 Select Coin:", reply_markup=InlineKeyboardMarkup(keyboard))
    return SET_COIN

async def save_coin(update, context):
    query = update.callback_query
    await query.answer()
    get_user_data(context)["coin"] = query.data.replace("coin_", "")
    return await settings_menu(update, context)

async def main_menu_callback(update, context):
    return await start(update, context)

async def settings_menu_msg(update, context):
    data = get_user_data(context)
    text = "⚙️ Settings Menu\nPlease ensure all settings are configured:"
    keyboard = [
        [InlineKeyboardButton(f"Bot name [{data['bot_name']}]", callback_data="set_bot_name")],
        [InlineKeyboardButton(f"Fiat [{data['fiat']}]", callback_data="set_fiat")],
        [InlineKeyboardButton(f"Pay Methods [{len(data['pay_methods'])}]", callback_data="set_pay_methods")],
        [InlineKeyboardButton(f"Coin [{data['coin']}]", callback_data="set_coin")],
        [InlineKeyboardButton(f"Max amount [{data['max_amount']}]", callback_data="set_max_amount")],
        [InlineKeyboardButton(f"Min amount [{data['min_amount']}]", callback_data="set_min_amount")],
        [InlineKeyboardButton(f"Target: [{data['target_type']}]", callback_data="set_target_type")],
        [InlineKeyboardButton(f"Target price/percent [Less {data['target_price']}]", callback_data="set_target_price")],
        [InlineKeyboardButton(f"Max num orders [{data['max_orders']}]", callback_data="set_max_orders")],
        [InlineKeyboardButton(f"Take Full bank orders [{'On' if data['take_full_bank'] else 'Off'}]", callback_data="toggle_take_full")],
        [InlineKeyboardButton("🔑 API Key", callback_data="set_api_key")],
        [InlineKeyboardButton("◀️ Back", callback_data="main_menu")],
    ]
    await update.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return SETTINGS_MENU

async def order_history(update, context):
    query = update.callback_query
    await query.answer()
    data = get_user_data(context)
    text = f"📋 Order History\n\nTotal: {data['total_orders']}\nThis Month: {data['monthly_orders']}\nVolume: {data['trading_volume']} {data['fiat']}"
    keyboard = [[InlineKeyboardButton("◀️ Back", callback_data="statistics")]]
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    return MAIN_MENU

async def renew(update, context):
    query = update.callback_query
    await query.answer()
    await query.edit_message_text(
        "🛒 Renew Subscription\n\nContact admin to renew.",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="statistics")]])
    )
    return MAIN_MENU

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv_handler = ConversationHandler(
        entry_points=[CommandHandler("start", start)],
        states={
            MAIN_MENU: [
                CallbackQueryHandler(start_bot, pattern="^start_bot$"),
                CallbackQueryHandler(stop_bot, pattern="^stop_bot$"),
                CallbackQueryHandler(settings_menu, pattern="^settings$"),
                CallbackQueryHandler(statistics, pattern="^statistics$"),
                CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"),
                CallbackQueryHandler(order_history, pattern="^order_history$"),
                CallbackQueryHandler(renew, pattern="^renew$"),
            ],
            SETTINGS_MENU: [
                CallbackQueryHandler(ask_bot_name, pattern="^set_bot_name$"),
                CallbackQueryHandler(ask_fiat, pattern="^set_fiat$"),
                CallbackQueryHandler(ask_pay_methods, pattern="^set_pay_methods$"),
                CallbackQueryHandler(set_coin, pattern="^set_coin$"),
                CallbackQueryHandler(ask_max_amount, pattern="^set_max_amount$"),
                CallbackQueryHandler(ask_min_amount, pattern="^set_min_amount$"),
                CallbackQueryHandler(set_target_type, pattern="^set_target_type$"),
                CallbackQueryHandler(ask_target_price, pattern="^set_target_price$"),
                CallbackQueryHandler(ask_max_orders, pattern="^set_max_orders$"),
                CallbackQueryHandler(toggle_take_full, pattern="^toggle_take_full$"),
                CallbackQueryHandler(ask_api_key, pattern="^set_api_key$"),
                CallbackQueryHandler(enter_api_key, pattern="^enter_api_key$"),
                CallbackQueryHandler(enter_secret_key, pattern="^enter_secret_key$"),
                CallbackQueryHandler(main_menu_callback, pattern="^main_menu$"),
                CallbackQueryHandler(save_fiat, pattern="^fiat_"),
                CallbackQueryHandler(save_coin, pattern="^coin_"),
                CallbackQueryHandler(save_target_type, pattern="^target_type_"),
                CallbackQueryHandler(toggle_pay_method, pattern="^pay_"),
            ],
            SET_BOT_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_bot_name),
                           CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_MAX_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_max_amount),
                             CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_MIN_AMOUNT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_min_amount),
                             CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_TARGET_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_target_price),
                               CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_MAX_ORDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_max_orders),
                             CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_API_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_api_key),
                          CallbackQueryHandler(ask_api_key, pattern="^set_api_key$")],
            SET_SECRET_KEY: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_secret_key),
                             CallbackQueryHandler(ask_api_key, pattern="^set_api_key$")],
            SET_FIAT: [CallbackQueryHandler(save_fiat, pattern="^fiat_"),
                       CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_COIN: [CallbackQueryHandler(save_coin, pattern="^coin_"),
                       CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_TARGET_TYPE: [CallbackQueryHandler(save_target_type, pattern="^target_type_"),
                              CallbackQueryHandler(settings_menu, pattern="^settings$")],
            SET_PAY_METHODS: [CallbackQueryHandler(toggle_pay_method, pattern="^pay_"),
                              CallbackQueryHandler(settings_menu, pattern="^settings$")],
        },
        fallbacks=[CommandHandler("start", start)],
        per_message=False,
    )
    app.add_handler(conv_handler)
    print("Bot is running with Auto-Buy feature...")
    app.run_polling()

if __name__ == "__main__":
    main()
