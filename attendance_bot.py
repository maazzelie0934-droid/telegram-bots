import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime
import threading

TOKEN = "8618917471:AAENUAZbnDX_IGm2NvHp1Fn0aPuHHWRoobI"
bot = telebot.TeleBot(TOKEN)

attendance = {}
active_timers = {}

def get_markup():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    btn1 = KeyboardButton("▶️ Start Work")
    btn2 = KeyboardButton("🍔 Eat")
    btn3 = KeyboardButton("🚻 Toilet")
    btn4 = KeyboardButton("🚬 Smoke")
    btn5 = KeyboardButton("🔲 Off Work")
    btn6 = KeyboardButton("💻 Off Day")
    btn7 = KeyboardButton("🪑 Back to Seat")
    markup.add(btn1, btn2)
    markup.add(btn3, btn4)
    markup.add(btn5, btn6)
    markup.add(btn7)
    return markup

def log_event(user_id, action):
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    if user_id not in attendance:
        attendance[user_id] = []
    attendance[user_id].append({"action": action, "time": now_str, "datetime": now})
    return now_str

def cancel_timer(user_id):
    if user_id in active_timers:
        active_timers[user_id].cancel()
        del active_timers[user_id]

def start_warning_timer(chat_id, user_id, name, action, minutes):
    cancel_timer(user_id)
    def warn():
        bot.send_message(
            chat_id,
            f"⚠️ *{name}* has been on {action} for *{minutes} minutes!*\n🪑 Please return to your seat now!",
            parse_mode="Markdown"
        )
    t = threading.Timer(minutes * 60, warn)
    t.daemon = True
    t.start()
    active_timers[user_id] = t

def get_work_duration(user_id):
    if user_id not in attendance:
        return None
    start_time = None
    end_time = None
    for entry in attendance[user_id]:
        if entry["action"] == "Start Work":
            start_time = entry["datetime"]
        if entry["action"] == "Off Work":
            end_time = entry["datetime"]
    if start_time and end_time:
        diff = end_time - start_time
        hours = diff.seconds // 3600
        minutes = (diff.seconds % 3600) // 60
        return hours, minutes
    return None

@bot.message_handler(commands=['start'])
def start(message):
    name = message.from_user.first_name
    bot.send_message(
        message.chat.id,
        f"👋 *Welcome {name}!*\n\n📋 *Employee Attendance System*\nPlease select your status:",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "▶️ Start Work")
def start_work(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Start Work")
    bot.send_message(
        message.chat.id,
        f"✅ *{name}* has started work!\n🕐 Time: {time}\n\n⏳ 12 hour shift started!",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "🍔 Eat")
def eat(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Eat")
    start_warning_timer(message.chat.id, user_id, name, "🍔 Eat Break", 40)
    bot.send_message(
        message.chat.id,
        f"🍔 *{name}* has gone for lunch!\n🕐 Time: {time}\n⏰ Please return within 40 minutes!",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "🚻 Toilet")
def toilet(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Toilet")
    start_warning_timer(message.chat.id, user_id, name, "🚻 Toilet Break", 15)
    bot.send_message(
        message.chat.id,
        f"🚻 *{name}* has gone to the toilet!\n🕐 Time: {time}\n⏰ Please return within 15 minutes!",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "🚬 Smoke")
def smoke(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Smoke")
    start_warning_timer(message.chat.id, user_id, name, "🚬 Smoke Break", 15)
    bot.send_message(
        message.chat.id,
        f"🚬 *{name}* has gone for a smoke break!\n🕐 Time: {time}\n⏰ Please return within 15 minutes!",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "🪑 Back to Seat")
def back_to_seat(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Back to Seat")
    bot.send_message(
        message.chat.id,
        f"🪑 *{name}* is back at their seat!\n🕐 Time: {time}",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "🔲 Off Work")
def off_work(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Off Work")
    duration = get_work_duration(user_id)
    if duration:
        hours, minutes = duration
        if hours < 12:
            extra = f"⚠️ *{name}* only worked *{hours} hours {minutes} minutes* — 12 hours not completed!"
        else:
            extra = f"✅ *{name}* worked *{hours} hours {minutes} minutes* — Great job!"
    else:
        extra = "⚠️ Start Work record not found!"
    bot.send_message(
        message.chat.id,
        f"🔲 *{name}* has ended work!\n🕐 Time: {time}\n\n{extra}\n\n📊 Type /report to see full report.",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(func=lambda msg: msg.text == "💻 Off Day")
def off_day(message):
    name = message.from_user.first_name
    user_id = message.from_user.id
    cancel_timer(user_id)
    time = log_event(user_id, "Off Day")
    bot.send_message(
        message.chat.id,
        f"💻 *{name}* is on Off Day today!\n🕐 Time: {time}",
        parse_mode="Markdown",
        reply_markup=get_markup()
    )

@bot.message_handler(commands=['report'])
def report(message):
    user_id = message.from_user.id
    name = message.from_user.first_name
    if user_id not in attendance or len(attendance[user_id]) == 0:
        bot.send_message(message.chat.id, "❌ No records found for today.")
        return
    report_text = f"📊 *{name}'s Today Report:*\n\n"
    for entry in attendance[user_id]:
        report_text += f"• {entry['action']} — `{entry['time']}`\n"
    duration = get_work_duration(user_id)
    if duration:
        hours, minutes = duration
        report_text += f"\n⏱ *Total Work Time: {hours} hours {minutes} minutes*"
    bot.send_message(message.chat.id, report_text, parse_mode="Markdown")

print("✅ Bot Running...")
bot.polling()
