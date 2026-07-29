import telebot
from telebot.types import ReplyKeyboardMarkup, KeyboardButton
from datetime import datetime
import threading
import json
import os

# It's safer to set BOT_TOKEN as an environment variable instead of hardcoding it.
TOKEN = os.getenv("BOT_TOKEN", "8618917471:AAENUAZbnDX_IGm2NvHp1Fn0aPuHHWRoobI")
bot = telebot.TeleBot(TOKEN)

DATA_FILE     = "attendance_data.json"
COUNTERS_FILE = "counters_data.json"
LABELS_FILE   = "labels_data.json"   # user_id -> anonymous "Employee N" label (admin-only mapping)

SHIFT_HOURS = 12  # expected shift length used for "leaving early" detection

# ── Group + Admin configuration ────────────────────────────────────────────────
# GROUP_CHAT_ID: the shared group where anonymized check-in/out updates get posted.
#   Set this as an environment variable once you know the group's chat id.
#   Tip: add the bot to the group, then send /groupid inside that group as an
#   admin — the bot will reply with the correct id to put in GROUP_CHAT_ID.
GROUP_CHAT_ID = os.getenv("GROUP_CHAT_ID")
if GROUP_CHAT_ID:
    try:
        GROUP_CHAT_ID = int(GROUP_CHAT_ID)
    except ValueError:
        GROUP_CHAT_ID = None

# ADMIN_IDS: comma-separated Telegram user ids allowed to see real names, e.g.
#   ADMIN_IDS=111111111,222222222
#   Tip: send /myid to the bot privately to find your own Telegram user id.
ADMIN_IDS = set()
for part in os.getenv("ADMIN_IDS", "").split(","):
    part = part.strip()
    if part.isdigit():
        ADMIN_IDS.add(int(part))

def is_admin(user_id):
    return user_id in ADMIN_IDS

active_timers = {}   # user_id -> threading.Timer (break overdue warning)
active_breaks = {}   # user_id -> {"type": "Eat"/"Toilet"/"Smoke", "start": datetime}

# ── Persistent load/save ──────────────────────────────────────────────────────

def load_json(path):
    if os.path.exists(path):
        try:
            with open(path, "r") as f:
                return json.load(f)
        except Exception:
            pass
    return {}

def save_json(path, data):
    try:
        with open(path, "w") as f:
            json.dump(data, f, indent=2, default=str)
    except Exception as e:
        print(f"Save error ({path}): {e}")

_attendance_raw = load_json(DATA_FILE)     # { "user_id": [ {action, time, datetime} ] }
_counters_raw   = load_json(COUNTERS_FILE) # { "user_id": {Eat: {count,seconds}, ...} }
_labels_raw     = load_json(LABELS_FILE)   # { "user_id": {"label": "Employee 3", "name": "John"} }

def get_attendance(user_id):
    return _attendance_raw.get(str(user_id), [])

def set_attendance(user_id, records):
    _attendance_raw[str(user_id)] = records
    save_json(DATA_FILE, _attendance_raw)

DEFAULT_COUNTERS = {
    "Eat":    {"count": 0, "seconds": 0},
    "Toilet": {"count": 0, "seconds": 0},
    "Smoke":  {"count": 0, "seconds": 0},
}

def get_counters(user_id):
    c = _counters_raw.get(str(user_id))
    if not c:
        return json.loads(json.dumps(DEFAULT_COUNTERS))
    for k, v in DEFAULT_COUNTERS.items():
        if k not in c:
            c[k] = dict(v)
    return c

def set_counters(user_id, c):
    _counters_raw[str(user_id)] = c
    save_json(COUNTERS_FILE, _counters_raw)

# ── Anonymous labels (identity hidden from everyone except admins) ────────────

def get_or_create_label(user_id, display_name):
    uid = str(user_id)
    entry = _labels_raw.get(uid)
    if entry is None:
        next_num = len(_labels_raw) + 1
        entry = {"label": f"Employee {next_num}", "name": display_name}
        _labels_raw[uid] = entry
        save_json(LABELS_FILE, _labels_raw)
    else:
        # keep the stored name up to date in case it changed on Telegram
        if entry.get("name") != display_name:
            entry["name"] = display_name
            _labels_raw[uid] = entry
            save_json(LABELS_FILE, _labels_raw)
    return entry["label"]

# ── Helpers ────────────────────────────────────────────────────────────────────

def fmt_hms(total_seconds):
    total_seconds = max(0, int(total_seconds))
    h = total_seconds // 3600
    m = (total_seconds % 3600) // 60
    s = total_seconds % 60
    return f"{h:02d}:{m:02d}:{s:02d}"

def get_markup():
    markup = ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add(KeyboardButton("🚀 Start Work"), KeyboardButton("🍕 Eat Break"))
    markup.add(KeyboardButton("🧻 Toilet"), KeyboardButton("💨 Smoke"))
    markup.add(KeyboardButton("🏁 Off Work"), KeyboardButton("🌅 Off Day"))
    markup.add(KeyboardButton("🧭 Back to Seat"))
    return markup

def log_event(user_id, action):
    now = datetime.now()
    now_str = now.strftime("%Y-%m-%d %H:%M:%S")
    records = get_attendance(user_id)
    records.append({"action": action, "time": now_str, "datetime": now_str})
    set_attendance(user_id, records)
    return now, now_str

def close_active_break(user_id):
    """If a break is currently open for this user, add its duration to the
    counters and clear it. Called before starting a new break, on 'Back to
    Seat', and (as a safety net) on 'Off Work'."""
    b = active_breaks.get(user_id)
    if not b:
        return
    duration = (datetime.now() - b["start"]).total_seconds()
    c = get_counters(user_id)
    c[b["type"]]["seconds"] += int(duration)
    set_counters(user_id, c)
    del active_breaks[user_id]

def start_break(user_id, break_type):
    close_active_break(user_id)
    c = get_counters(user_id)
    c[break_type]["count"] += 1
    set_counters(user_id, c)
    active_breaks[user_id] = {"type": break_type, "start": datetime.now()}

def cancel_timer(user_id):
    if user_id in active_timers:
        active_timers[user_id].cancel()
        del active_timers[user_id]

def start_warning_timer(chat_id, user_id, label, action, minutes):
    cancel_timer(user_id)
    def warn():
        bot.send_message(
            chat_id,
            f"⚠️ You have been on {action} for {minutes} minutes!\n🧭 Please return to your seat now!"
        )
    t = threading.Timer(minutes * 60, warn)
    t.daemon = True
    t.start()
    active_timers[user_id] = t

def get_work_times(user_id):
    """Returns (start_datetime, end_datetime) for today's Start Work / Off Work."""
    records = get_attendance(user_id)
    start_time = None
    end_time   = None
    for entry in records:
        if entry["action"] == "Start Work":
            start_time = datetime.strptime(entry["datetime"], "%Y-%m-%d %H:%M:%S")
        if entry["action"] == "Off Work":
            end_time = datetime.strptime(entry["datetime"], "%Y-%m-%d %H:%M:%S")
    return start_time, end_time

def counters_summary_text(user_id):
    c = get_counters(user_id)
    total_activity_seconds = sum(v["seconds"] for v in c.values())
    lines = [f"Total time for all activities today: {fmt_hms(total_activity_seconds)}"]
    for lbl in ["Eat", "Toilet", "Smoke"]:
        lines.append(f"Total {lbl} count today: {c[lbl]['count']} time(s)")
        lines.append(f"Total {lbl} time today: {fmt_hms(c[lbl]['seconds'])}")
    return "\n".join(lines), total_activity_seconds

def post_to_group(text):
    """Broadcast an anonymized update to the shared monitoring group, if configured."""
    if not GROUP_CHAT_ID:
        return
    try:
        bot.send_message(GROUP_CHAT_ID, text)
    except Exception as e:
        print(f"Group post error: {e}")

DIVIDER = "――――――――――――――"

# ── Handlers ────────────────────────────────────────────────────────────────────

@bot.message_handler(commands=['start'])
def start(message):
    if message.chat.type != "private":
        return  # only respond to the personal check-in flow in private chats
    name = message.from_user.first_name
    bot.send_message(
        message.chat.id,
        f"👋 Welcome {name}!\n\n📋 Employee Attendance System\nPlease select your status:",
        reply_markup=get_markup()
    )

@bot.message_handler(commands=['myid'])
def myid(message):
    bot.send_message(message.chat.id, f"Your Telegram user ID is: {message.from_user.id}")

@bot.message_handler(commands=['groupid'])
def groupid(message):
    bot.send_message(message.chat.id, f"This chat's ID is: {message.chat.id}")

@bot.message_handler(commands=['staff'])
def staff(message):
    """Admin-only: reveals which real person is behind each anonymous label."""
    if message.chat.type != "private" or not is_admin(message.from_user.id):
        return
    if not _labels_raw:
        bot.send_message(message.chat.id, "No staff registered yet.")
        return
    lines = ["👥 Staff directory (admin-only):", DIVIDER]
    for uid, entry in _labels_raw.items():
        lines.append(f"{entry['label']} — {entry['name']} (ID: {uid})")
    bot.send_message(message.chat.id, "\n".join(lines))

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "🚀 Start Work")
def start_work(message):
    user_id = message.from_user.id
    label = get_or_create_label(user_id, message.from_user.first_name)
    cancel_timer(user_id)
    active_breaks.pop(user_id, None)
    set_counters(user_id, json.loads(json.dumps(DEFAULT_COUNTERS)))  # new day, reset counters
    now, time_str = log_event(user_id, "Start Work")

    bot.send_message(
        message.chat.id,
        f"🚀 Start Work\n\n"
        f"{DIVIDER}\n"
        f"✅ Check-In Succeeded: Start Work - {now.strftime('%m/%d %H:%M:%S')}\n"
        f"⏳ {SHIFT_HOURS} hour shift started!",
        reply_markup=get_markup()
    )
    post_to_group(
        f"🚀 {label} started work!\n"
        f"🕐 Time: {now.strftime('%m/%d %H:%M:%S')}"
    )

def handle_break(message, action, minutes):
    user_id = message.from_user.id
    label = get_or_create_label(user_id, message.from_user.first_name)
    cancel_timer(user_id)
    start_break(user_id, action)
    now, time_str = log_event(user_id, action)
    start_warning_timer(message.chat.id, user_id, label, action, minutes)
    counters_text, _ = counters_summary_text(user_id)
    emoji = {"Eat": "🍕", "Toilet": "🧻", "Smoke": "💨"}[action]

    bot.send_message(
        message.chat.id,
        f"{emoji} {action} Break\n\n"
        f"{DIVIDER}\n"
        f"✅ Check-In Succeeded: {action} - {now.strftime('%m/%d %H:%M:%S')}\n"
        f"⏰ Please return within {minutes} minutes!\n"
        f"{DIVIDER}\n"
        f"{counters_text}",
        reply_markup=get_markup()
    )
    post_to_group(
        f"{emoji} {label} went for a {action} break.\n"
        f"🕐 Time: {now.strftime('%m/%d %H:%M:%S')} — expected back within {minutes} min."
    )

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "🍕 Eat Break")
def eat(message):
    handle_break(message, "Eat", 30)

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "🧻 Toilet")
def toilet(message):
    handle_break(message, "Toilet", 15)

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "💨 Smoke")
def smoke(message):
    handle_break(message, "Smoke", 15)

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "🧭 Back to Seat")
def back_to_seat(message):
    user_id = message.from_user.id
    label = get_or_create_label(user_id, message.from_user.first_name)
    cancel_timer(user_id)
    close_active_break(user_id)
    now, time_str = log_event(user_id, "Back to Seat")

    bot.send_message(
        message.chat.id,
        f"🧭 Back to Seat\n\n"
        f"{DIVIDER}\n"
        f"✅ Check-In Succeeded: Back to Seat - {now.strftime('%m/%d %H:%M:%S')}",
        reply_markup=get_markup()
    )
    post_to_group(
        f"🧭 {label} is back at their seat.\n"
        f"🕐 Time: {now.strftime('%m/%d %H:%M:%S')}"
    )

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "🏁 Off Work")
def off_work(message):
    user_id = message.from_user.id
    label = get_or_create_label(user_id, message.from_user.first_name)
    cancel_timer(user_id)
    close_active_break(user_id)  # safety net if a break was left open
    now, time_str = log_event(user_id, "Off Work")

    start_time, end_time = get_work_times(user_id)
    counters_text, total_activity_seconds = counters_summary_text(user_id)

    if start_time and end_time:
        work_seconds  = int((end_time - start_time).total_seconds())
        pure_seconds  = max(0, work_seconds - total_activity_seconds)
        shift_seconds = SHIFT_HOURS * 3600

        if work_seconds < shift_seconds:
            early_seconds = shift_seconds - work_seconds
            status_block = (
                f"⚠️ Warning: Left early!\n"
                f"Duration of Leaving Early: {fmt_hms(early_seconds)}\n"
                f"✅ Check-In Succeeded: Off Work - {now.strftime('%m/%d %H:%M:%S')}"
            )
            group_status = f"⚠️ left early by {fmt_hms(early_seconds)}"
        else:
            status_block = (
                f"✅ Great job, full shift completed!\n"
                f"✅ Check-In Succeeded: Off Work - {now.strftime('%m/%d %H:%M:%S')}"
            )
            group_status = "✅ completed a full shift"

        private_body = (
            f"🏁 Off Work\n\n"
            f"{DIVIDER}\n"
            f"{status_block}\n"
            f"{DIVIDER}\n"
            f"Total work time today: {fmt_hms(work_seconds)}\n"
            f"Pure work time: {fmt_hms(pure_seconds)}\n"
            f"{counters_text}"
        )
        group_body = (
            f"🏁 {label} ended work — {group_status}.\n"
            f"🕐 Time: {now.strftime('%m/%d %H:%M:%S')}\n"
            f"Total work time: {fmt_hms(work_seconds)} | Pure work time: {fmt_hms(pure_seconds)}"
        )
    else:
        private_body = (
            f"🏁 Off Work\n\n"
            f"{DIVIDER}\n"
            f"⚠️ Start Work record not found!\n"
            f"✅ Check-In Succeeded: Off Work - {now.strftime('%m/%d %H:%M:%S')}\n"
            f"{DIVIDER}\n"
            f"{counters_text}"
        )
        group_body = f"🏁 {label} ended work.\n🕐 Time: {now.strftime('%m/%d %H:%M:%S')}"

    bot.send_message(message.chat.id, private_body, reply_markup=get_markup())
    bot.send_message(message.chat.id, "📊 Type /report to see your full report.")
    post_to_group(group_body)

@bot.message_handler(func=lambda msg: msg.chat.type == "private" and msg.text == "🌅 Off Day")
def off_day(message):
    user_id = message.from_user.id
    label = get_or_create_label(user_id, message.from_user.first_name)
    cancel_timer(user_id)
    active_breaks.pop(user_id, None)
    now, time_str = log_event(user_id, "Off Day")

    bot.send_message(
        message.chat.id,
        f"🌅 Off Day\n\n"
        f"{DIVIDER}\n"
        f"✅ Check-In Succeeded: Off Day - {now.strftime('%m/%d %H:%M:%S')}",
        reply_markup=get_markup()
    )
    post_to_group(f"🌅 {label} is on Off Day today.")

@bot.message_handler(commands=['report'])
def report(message):
    if message.chat.type != "private":
        return
    user_id = message.from_user.id
    records = get_attendance(user_id)
    if not records:
        bot.send_message(message.chat.id, "❌ No records found for today.")
        return

    report_text = f"📊 Your Today Report\n\n{DIVIDER}\n"
    for entry in records:
        report_text += f"• {entry['action']} — {entry['time']}\n"

    start_time, end_time = get_work_times(user_id)
    counters_text, total_activity_seconds = counters_summary_text(user_id)

    report_text += f"{DIVIDER}\n"
    if start_time and end_time:
        work_seconds = int((end_time - start_time).total_seconds())
        pure_seconds = max(0, work_seconds - total_activity_seconds)
        report_text += f"Total work time today: {fmt_hms(work_seconds)}\n"
        report_text += f"Pure work time: {fmt_hms(pure_seconds)}\n"
    report_text += counters_text

    bot.send_message(message.chat.id, report_text)

print("✅ Bot Running...")
bot.polling(none_stop=True)
