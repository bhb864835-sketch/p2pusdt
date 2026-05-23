import logging
import asyncio
import aiohttp
import hmac
import hashlib
import time
import os
import json
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    MessageHandler, filters, ContextTypes, ConversationHandler
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BOT_TOKEN = os.environ.get("BOT_TOKEN", "8854058497:AAFRurkSRIP0tEmL2XACuAVLfsznbtMFciM")

(MAIN, SETTINGS, S_NAME, S_FIAT, S_PAY, S_COIN,
 S_MAX, S_MIN, S_TARGET, S_ORDERS, S_API, S_SECRET) = range(12)

PAY_LIST = ["KBZPay","WavePay","WaveMoney","AYAPay","CBPay",
            "UABPay","BankTransfer","CashDeposit","WaveMobile","SpecificBank"]

def init(ctx):
    if "d" not in ctx.user_data:
        ctx.user_data["d"] = {
            "name":"My P2P Bot","fiat":"MMK","pays":[],
            "coin":"USDT","max":15000000,"min":10000,
            "target":4500.0,"orders":10,"full":False,
            "api":"","secret":"","running":False,
            "total":0,"monthly":0,"volume":0,
            "sub":"03.01.2028",
            "bid":"BOT"+str(abs(hash(str(time.time()))))[:6]
        }
    return ctx.user_data["d"]

def make_signature(secret, params):
    return hmac.new(secret.encode(), params.encode(), hashlib.sha256).hexdigest()

async def main_menu(update, context):
    d = init(context)
    status = "Active 🟢" if d["running"] else "Stopped 🔴"
    text = (f"🤖 Welcome to {d['name']}!\n\n"
            f"Status: {status}\n\n"
            f"BotID: {d['bid']}")
    if d["running"]:
        kb = [[InlineKeyboardButton("⏹ Stop Bot", callback_data="stop")],
              [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
              [InlineKeyboardButton("📋 Statistics", callback_data="stats")]]
    else:
        kb = [[InlineKeyboardButton("🚀 Start Bot", callback_data="start")],
              [InlineKeyboardButton("⚙️ Settings", callback_data="settings")],
              [InlineKeyboardButton("📋 Statistics", callback_data="stats")]]
    rm = InlineKeyboardMarkup(kb)
    if update.message:
        await update.message.reply_text(text, reply_markup=rm)
    else:
        await update.callback_query.edit_message_text(text, reply_markup=rm)
    return MAIN

async def cb_start(update, context):
    q = update.callback_query
    await q.answer()
    d = init(context)
    if not d["api"] or not d["secret"]:
        await q.edit_message_text(
            "⚠️ API Key မထည့်ရသေးဘူး!\nSettings → API Key သွားပါ",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Back", callback_data="menu")]]))
        return MAIN
    d["running"] = True
    uid = q.from_user.id
    asyncio.create_task(bot_loop(context, uid, d))
    return await main_menu(update, context)

async def cb_stop(update, context):
    q = update.callback_query
    await q.answer()
    d = init(context)
    d["running"] = False
    await context.bot.send_message(q.from_user.id, "⏹ Bot Stopped!")
    return await main_menu(update, context)

async def bot_loop(context, uid, d):
    await context.bot.send_message(uid, "🚀 Bot Started! Scanning P2P every 15 seconds...")
    scan_count = 0
    while d.get("running"):
        try:
            scan_count += 1
            await scan_and_buy(context, uid, d, scan_count)
        except Exception as e:
            logger.error(f"Loop error: {e}", exc_info=True)
            await context.bot.send_message(uid, f"⚠️ Error: {str(e)[:100]}")
        await asyncio.sleep(15)

async def get_p2p_ads(session, d):
    """Get P2P ads using authenticated Binance API"""
    ts = int(time.time() * 1000)
    
    # Method 1: Try authenticated search
    params = f"asset={d['coin']}&fiat={d['fiat']}&tradeType=BUY&timestamp={ts}"
    sig = make_signature(d["secret"], params)
    
    headers = {
        "X-MBX-APIKEY": d["api"],
        "Content-Type": "application/json"
    }
    
    # Try Binance C2C API
    url = f"https://api.binance.com/sapi/v1/c2c/ads/search?{params}&signature={sig}"
    
    try:
        async with session.get(url, headers=headers) as r:
            text = await r.text()
            logger.info(f"Auth API response: {r.status} - {text[:200]}")
            if r.status == 200:
                data = json.loads(text)
                if data.get("data"):
                    return data["data"]
    except Exception as e:
        logger.error(f"Auth API error: {e}")
    
    # Method 2: Try public P2P with cookies from API
    payload = {
        "proMerchantAds": False,
        "page": 1,
        "rows": 20,
        "payTypes": d["pays"] if d["pays"] else [],
        "asset": d["coin"],
        "fiat": d["fiat"],
        "tradeType": "BUY",
        "merchantCheck": False
    }
    
    headers2 = {
        "Content-Type": "application/json",
        "User-Agent": "Mozilla/5.0 (Linux; Android 13; SM-G991B) AppleWebKit/537.36",
        "clienttype": "android",
        "lang": "en",
        "Accept": "application/json"
    }
    
    try:
        async with session.post(
            "https://p2p.binance.com/bapi/c2c/v2/friendly/c2c/adv/search",
            json=payload,
            headers=headers2
        ) as r:
            text = await r.text()
            logger.info(f"P2P API response: {r.status} len={len(text)} - {text[:200]}")
            if r.status == 200:
                data = json.loads(text)
                ads = data.get("data", [])
                if ads:
                    return [{"price": float(a["adv"]["price"]),
                            "advNo": a["adv"]["advNo"],
                            "minAmount": float(a["adv"]["minSingleTransAmount"]),
                            "maxAmount": float(a["adv"]["maxSingleTransAmount"])} for a in ads]
    except Exception as e:
        logger.error(f"P2P API error: {e}")
    
    return []

async def place_p2p_order(session, d, adv_no, amount, price, context, uid):
    """Place P2P buy order"""
    try:
        ts = int(time.time() * 1000)
        
        # Try Binance C2C order API
        params = f"advNo={adv_no}&tradeType=BUY&asset={d['coin']}&fiatUnit={d['fiat']}&amount={int(amount)}&price={price}&timestamp={ts}"
        sig = make_signature(d["secret"], params)
        
        headers = {
            "X-MBX-APIKEY": d["api"],
            "Content-Type": "application/x-www-form-urlencoded"
        }
        
        body = f"{params}&signature={sig}"
        
        url = "https://api.binance.com/sapi/v1/c2c/orderMatch/placeOrder"
        
        async with session.post(url, data=body, headers=headers) as r:
            text = await r.text()
            logger.info(f"Order response: {r.status} - {text[:300]}")
            data = json.loads(text)
            
            if data.get("code") == "000000" or data.get("success"):
                return True, text
            else:
                # Try alternative P2P order endpoint
                payload = {
                    "advNo": adv_no,
                    "tradeType": "BUY",
                    "asset": d["coin"],
                    "fiatUnit": d["fiat"],
                    "amount": str(int(amount)),
                    "price": str(price),
                    "timestamp": ts
                }
                
                order_params = "&".join([f"{k}={v}" for k, v in sorted(payload.items())])
                order_sig = make_signature(d["secret"], order_params)
                payload["signature"] = order_sig
                
                headers2 = {
                    "Content-Type": "application/json",
                    "X-MBX-APIKEY": d["api"]
                }
                
                async with session.post(
                    "https://p2p.binance.com/bapi/c2c/v1/private/c2c/order/create",
                    json=payload,
                    headers=headers2
                ) as r2:
                    text2 = await r2.text()
                    logger.info(f"Alt order response: {r2.status} - {text2[:300]}")
                    data2 = json.loads(text2)
                    if data2.get("success") or data2.get("code") == "000000":
                        return True, text2
                    return False, text2
                    
    except Exception as e:
        logger.error(f"Order error: {e}", exc_info=True)
        return False, str(e)

async def scan_and_buy(context, uid, d, scan_count):
    timeout = aiohttp.ClientTimeout(total=30)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        ads = await get_p2p_ads(session, d)
        
        if not ads:
            if scan_count % 4 == 0:  # Every minute
                await context.bot.send_message(uid, f"🔍 Scanning... No ads (target: {d['target']} MMK)")
            return
        
        # Filter ads below target
        cheap_ads = []
        for ad in ads:
            price = float(ad.get("price", 0))
            if price < d["target"]:
                cheap_ads.append(ad)
        
        if not cheap_ads:
            best = float(ads[0].get("price", 0)) if ads else 0
            if scan_count % 4 == 0:
                await context.bot.send_message(uid, f"📊 Best: {best:.0f} MMK (target: {d['target']:.0f} MMK)")
            return
        
        await context.bot.send_message(uid, f"🎯 Found {len(cheap_ads)} ads below {d['target']:.0f} MMK!")
        
        count = 0
        for ad in cheap_ads:
            if count >= d["orders"]:
                break
            
            price = float(ad.get("price", 0))
            adv_no = ad.get("advNo", "")
            min_amt = float(ad.get("minAmount", 0))
            max_amt = float(ad.get("maxAmount", 0))
            
            buy_amt = min(d["max"], max_amt)
            buy_amt = max(buy_amt, min_amt)
            
            if buy_amt < min_amt or buy_amt > max_amt:
                continue
            
            ok, response = await place_p2p_order(session, d, adv_no, buy_amt, price, context, uid)
            
            if ok:
                diff = price - d["target"]
                msg = (f"🟢 SUCCESS!\n\n"
                       f"💰 Diff: {diff:.1f}\n"
                       f"📊 Rate: {price:.0f} MMK\n"
                       f"💵 Amount: {buy_amt:.0f} MMK")
                await context.bot.send_message(uid, msg)
                d["total"] += 1
                d["monthly"] += 1
                d["volume"] += buy_amt
                count += 1
            else:
                await context.bot.send_message(uid, f"❌ Order failed: {response[:100]}")

async def cb_stats(update, context):
    q = update.callback_query
    await q.answer()
    d = init(context)
    text = (f"📊 Statistics\n\n"
            f"📅 Subscription: {d['sub']}\n"
            f"📈 This Month: {d['monthly']}\n"
            f"🕐 Total Orders: {d['total']}\n"
            f"💰 Volume: {d['volume']} MMK")
    kb = [[InlineKeyboardButton("◀️ Back", callback_data="menu")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    return MAIN

async def cb_settings(update, context):
    q = update.callback_query
    await q.answer()
    d = init(context)
    if d["running"]:
        await q.answer("🚫 Bot ရပ်မှ Settings ပြောင်းလို့ရမည်", show_alert=True)
        return MAIN
    api_ok = "✅" if d["api"] else "❌"
    sec_ok = "✅" if d["secret"] else "❌"
    kb = [
        [InlineKeyboardButton(f"Bot name [{d['name']}]", callback_data="s_name")],
        [InlineKeyboardButton(f"Fiat [{d['fiat']}]", callback_data="s_fiat")],
        [InlineKeyboardButton(f"Pay Methods [{len(d['pays'])}]", callback_data="s_pay")],
        [InlineKeyboardButton(f"Coin [{d['coin']}]", callback_data="s_coin")],
        [InlineKeyboardButton(f"Max Amount [{d['max']}]", callback_data="s_max")],
        [InlineKeyboardButton(f"Min Amount [{d['min']}]", callback_data="s_min")],
        [InlineKeyboardButton(f"Target Price [{d['target']}]", callback_data="s_target")],
        [InlineKeyboardButton(f"Max Orders [{d['orders']}]", callback_data="s_orders")],
        [InlineKeyboardButton(f"API [{api_ok}] Secret [{sec_ok}]", callback_data="s_api")],
        [InlineKeyboardButton("◀️ Back", callback_data="menu")],
    ]
    await q.edit_message_text("⚙️ Settings Menu", reply_markup=InlineKeyboardMarkup(kb))
    return SETTINGS

async def s_name(update, context):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("✏️ Bot နာမည် ထည့်ပါ:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]]))
    return S_NAME

async def save_name(update, context):
    init(context)["name"] = update.message.text
    await update.message.reply_text("✅ Saved!")
    return await show_settings(update, context)

async def s_fiat(update, context):
    q = update.callback_query; await q.answer()
    kb = [[InlineKeyboardButton("MMK", callback_data="f_MMK"),
           InlineKeyboardButton("USD", callback_data="f_USD")],
          [InlineKeyboardButton("◀️ Back", callback_data="settings")]]
    await q.edit_message_text("💱 Fiat ရွေးပါ:", reply_markup=InlineKeyboardMarkup(kb))
    return S_FIAT

async def save_fiat(update, context):
    q = update.callback_query; await q.answer()
    init(context)["fiat"] = q.data.replace("f_", "")
    return await cb_settings(update, context)

async def s_pay(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    sel = d["pays"]
    kb = [[InlineKeyboardButton("✅ Choose All", callback_data="pay_ALL")]]
    for m in PAY_LIST:
        mark = "✅ " if m in sel else ""
        kb.append([InlineKeyboardButton(f"{mark}{m}", callback_data=f"pay_{m}")])
    kb.append([InlineKeyboardButton("◀️ Back", callback_data="settings")])
    await q.edit_message_text("💳 Payment Methods:", reply_markup=InlineKeyboardMarkup(kb))
    return S_PAY

async def toggle_pay(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    m = q.data.replace("pay_", "")
    if m == "ALL":
        d["pays"] = [] if len(d["pays"]) == len(PAY_LIST) else PAY_LIST.copy()
    elif m in d["pays"]:
        d["pays"].remove(m)
    else:
        d["pays"].append(m)
    return await s_pay(update, context)

async def s_coin(update, context):
    q = update.callback_query; await q.answer()
    kb = [[InlineKeyboardButton("USDT", callback_data="c_USDT"),
           InlineKeyboardButton("BTC", callback_data="c_BTC")],
          [InlineKeyboardButton("◀️ Back", callback_data="settings")]]
    await q.edit_message_text("🪙 Coin ရွေးပါ:", reply_markup=InlineKeyboardMarkup(kb))
    return S_COIN

async def save_coin(update, context):
    q = update.callback_query; await q.answer()
    init(context)["coin"] = q.data.replace("c_", "")
    return await cb_settings(update, context)

async def s_max(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    await q.edit_message_text(f"💰 Max Amount (MMK):\nCurrent: {d['max']}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]]))
    return S_MAX

async def save_max(update, context):
    try:
        init(context)["max"] = int(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await show_settings(update, context)

async def s_min(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    await q.edit_message_text(f"💰 Min Amount (MMK):\nCurrent: {d['min']}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]]))
    return S_MIN

async def save_min(update, context):
    try:
        init(context)["min"] = int(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await show_settings(update, context)

async def s_target(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    await q.edit_message_text(f"🎯 Target Price (MMK):\nCurrent: {d['target']}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]]))
    return S_TARGET

async def save_target(update, context):
    try:
        init(context)["target"] = float(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await show_settings(update, context)

async def s_orders(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    await q.edit_message_text(f"🔢 Max Orders:\nCurrent: {d['orders']}",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="settings")]]))
    return S_ORDERS

async def save_orders(update, context):
    try:
        init(context)["orders"] = int(update.message.text)
        await update.message.reply_text("✅ Saved!")
    except:
        await update.message.reply_text("❌ Numbers only!")
    return await show_settings(update, context)

async def s_api(update, context):
    q = update.callback_query; await q.answer()
    d = init(context)
    a = "✅ Set" if d["api"] else "❌ Not Set"
    s = "✅ Set" if d["secret"] else "❌ Not Set"
    kb = [[InlineKeyboardButton("+ Add API Key", callback_data="enter_api")],
          [InlineKeyboardButton("+ Add Secret Key", callback_data="enter_secret")],
          [InlineKeyboardButton("◀️ Back", callback_data="settings")]]
    await q.edit_message_text(f"🔑 API Menu\n\nAPI: {a}\nSecret: {s}",
        reply_markup=InlineKeyboardMarkup(kb))
    return SETTINGS

async def enter_api(update, context):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("🔑 Binance API Key ထည့်ပါ:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="s_api")]]))
    return S_API

async def save_api(update, context):
    init(context)["api"] = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass
    await update.message.reply_text("✅ API Key saved!")
    return await show_settings(update, context)

async def enter_secret(update, context):
    q = update.callback_query; await q.answer()
    await q.edit_message_text("🔐 Binance Secret Key ထည့်ပါ:",
        reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Cancel", callback_data="s_api")]]))
    return S_SECRET

async def save_secret(update, context):
    init(context)["secret"] = update.message.text.strip()
    try:
        await update.message.delete()
    except:
        pass
    await update.message.reply_text("✅ Secret Key saved!")
    return await show_settings(update, context)

async def show_settings(update, context):
    d = init(context)
    api_ok = "✅" if d["api"] else "❌"
    sec_ok = "✅" if d["secret"] else "❌"
    kb = [
        [InlineKeyboardButton(f"Bot name [{d['name']}]", callback_data="s_name")],
        [InlineKeyboardButton(f"Fiat [{d['fiat']}]", callback_data="s_fiat")],
        [InlineKeyboardButton(f"Pay Methods [{len(d['pays'])}]", callback_data="s_pay")],
        [InlineKeyboardButton(f"Coin [{d['coin']}]", callback_data="s_coin")],
        [InlineKeyboardButton(f"Max Amount [{d['max']}]", callback_data="s_max")],
        [InlineKeyboardButton(f"Min Amount [{d['min']}]", callback_data="s_min")],
        [InlineKeyboardButton(f"Target Price [{d['target']}]", callback_data="s_target")],
        [InlineKeyboardButton(f"Max Orders [{d['orders']}]", callback_data="s_orders")],
        [InlineKeyboardButton(f"API [{api_ok}] Secret [{sec_ok}]", callback_data="s_api")],
        [InlineKeyboardButton("◀️ Back", callback_data="menu")],
    ]
    await update.message.reply_text("⚙️ Settings Menu", reply_markup=InlineKeyboardMarkup(kb))
    return SETTINGS

async def cb_menu(update, context):
    return await main_menu(update, context)

def main():
    app = Application.builder().token(BOT_TOKEN).build()
    conv = ConversationHandler(
        entry_points=[CommandHandler("start", main_menu)],
        states={
            MAIN: [
                CallbackQueryHandler(cb_start, pattern="^start$"),
                CallbackQueryHandler(cb_stop, pattern="^stop$"),
                CallbackQueryHandler(cb_settings, pattern="^settings$"),
                CallbackQueryHandler(cb_stats, pattern="^stats$"),
                CallbackQueryHandler(cb_menu, pattern="^menu$"),
            ],
            SETTINGS: [
                CallbackQueryHandler(s_name, pattern="^s_name$"),
                CallbackQueryHandler(s_fiat, pattern="^s_fiat$"),
                CallbackQueryHandler(s_pay, pattern="^s_pay$"),
                CallbackQueryHandler(s_coin, pattern="^s_coin$"),
                CallbackQueryHandler(s_max, pattern="^s_max$"),
                CallbackQueryHandler(s_min, pattern="^s_min$"),
                CallbackQueryHandler(s_target, pattern="^s_target$"),
                CallbackQueryHandler(s_orders, pattern="^s_orders$"),
                CallbackQueryHandler(s_api, pattern="^s_api$"),
                CallbackQueryHandler(enter_api, pattern="^enter_api$"),
                CallbackQueryHandler(enter_secret, pattern="^enter_secret$"),
                CallbackQueryHandler(cb_menu, pattern="^menu$"),
                CallbackQueryHandler(save_fiat, pattern="^f_"),
                CallbackQueryHandler(save_coin, pattern="^c_"),
                CallbackQueryHandler(toggle_pay, pattern="^pay_"),
            ],
            S_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_name),
                     CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_MAX: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_max),
                    CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_MIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_min),
                    CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_TARGET: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_target),
                       CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_ORDERS: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_orders),
                       CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_API: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_api),
                    CallbackQueryHandler(s_api, pattern="^s_api$")],
            S_SECRET: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_secret),
                       CallbackQueryHandler(s_api, pattern="^s_api$")],
            S_FIAT: [CallbackQueryHandler(save_fiat, pattern="^f_"),
                     CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_COIN: [CallbackQueryHandler(save_coin, pattern="^c_"),
                     CallbackQueryHandler(cb_settings, pattern="^settings$")],
            S_PAY: [CallbackQueryHandler(toggle_pay, pattern="^pay_"),
                    CallbackQueryHandler(cb_settings, pattern="^settings$")],
        },
        fallbacks=[CommandHandler("start", main_menu)],
        per_message=False,
    )
    app.add_handler(conv)
    print("Bot running with authenticated P2P search...")
    app.run_polling()

if __name__ == "__main__":
    main()
