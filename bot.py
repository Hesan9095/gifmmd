import telebot
import sqlite3
import time
from datetime import datetime, timedelta
import schedule
import threading
from tenacity import retry, stop_after_attempt, wait_fixed, retry_if_exception_type
import requests
from telebot.apihelper import ApiTelegramException

TOKEN = "7505098396:AAFDQb0emCA8yBKL3ou7-_G2vSDwL2xDyt0"
CHANNEL_ID = "-1002230740786"
BOT_USERNAME = "gifmmd_bot"
ADMIN_USER_ID = 7373449365

bot = telebot.TeleBot(TOKEN)

def adapt_datetime(dt):
    return dt.isoformat()

def convert_datetime(s):
    return datetime.fromisoformat(s.decode() if isinstance(s, bytes) else s)

sqlite3.register_adapter(datetime, adapt_datetime)
sqlite3.register_converter('DATETIME', convert_datetime)

conn = sqlite3.connect('database.db', check_same_thread=False, detect_types=sqlite3.PARSE_DECLTYPES)
cursor = conn.cursor()

# اضافه کردن ستون user_id در صورت نبود
try:
    cursor.execute('SELECT user_id FROM gifs LIMIT 1')
except sqlite3.OperationalError:
    cursor.execute('ALTER TABLE gifs ADD COLUMN user_id INTEGER')
    conn.commit()

cursor.execute('''
CREATE TABLE IF NOT EXISTS gifs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    gif_file_id TEXT,
    caption TEXT,
    scheduled_time DATETIME,
    likes INTEGER DEFAULT 0,
    dislikes INTEGER DEFAULT 0,
    poll_message_id INTEGER,
    approved BOOLEAN DEFAULT FALSE,
    user_id INTEGER
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS votes (
    user_id INTEGER,
    gif_id INTEGER,
    vote_type TEXT,
    PRIMARY KEY (user_id, gif_id)
)
''')

cursor.execute('''
CREATE TABLE IF NOT EXISTS admins (
    user_id INTEGER PRIMARY KEY
)
''')

cursor.execute('INSERT OR IGNORE INTO admins (user_id) VALUES (?)', (ADMIN_USER_ID,))
conn.commit()

react_all_enabled = False
user_states = {}

def is_admin(user_id):
    with sqlite3.connect('database.db') as conn_temp:
        cursor_temp = conn_temp.cursor()
        cursor_temp.execute('SELECT user_id FROM admins WHERE user_id = ?', (user_id,))
        return cursor_temp.fetchone() is not None

@retry(stop=stop_after_attempt(3), wait=wait_fixed(5),
       retry=retry_if_exception_type((requests.exceptions.ConnectTimeout, ApiTelegramException)))
def safe_api_call(method, *args, **kwargs):
    return method(*args, **kwargs)

# دستورات ادمین
@bot.message_handler(commands=['id'])
def get_user_id(message):
    if not is_admin(message.from_user.id):
        return
    safe_api_call(bot.reply_to, message, f"آیدی شما: {message.from_user.id}")

@bot.message_handler(commands=['setadmin'])
def set_admin(message):
    if not is_admin(message.from_user.id):
        return
    user_id = message.from_user.id
    cursor.execute('INSERT OR REPLACE INTO admins (user_id) VALUES (?)', (user_id,))
    conn.commit()
    safe_api_call(bot.reply_to, message, f"شما ({user_id}) به‌عنوان ادمین تنظیم شدید!")

@bot.message_handler(commands=['react_all'])
def enable_react_all(message):
    if not is_admin(message.from_user.id):
        return
    global react_all_enabled
    react_all_enabled = True
    safe_api_call(bot.reply_to, message, "از این به بعد روی همه پیام‌های جدید تو کانال ری‌اکشن ❤️ زده میشه!")

@bot.message_handler(commands=['stop_react_all'])
def disable_react_all(message):
    if not is_admin(message.from_user.id):
        return
    global react_all_enabled
    react_all_enabled = False
    safe_api_call(bot.reply_to, message, "ری‌اکشن خودکار روی پیام‌های جدید متوقف شد!")

@bot.channel_post_handler(content_types=['text', 'animation', 'photo', 'video'])
def react_to_channel_post(message):
    if not react_all_enabled:
        return
    try:
        reaction = telebot.types.ReactionTypeEmoji(emoji="❤️")
        safe_api_call(bot.set_message_reaction,
                      chat_id=message.chat.id,
                      message_id=message.message_id,
                      reaction=[reaction])
    except Exception as e:
        print(f"Error in reaction: {e}")

# استارت
@bot.message_handler(commands=['start'])
def send_welcome(message):
    if message.text.startswith("/start vote_"):
        gif_id = int(message.text.split("_")[1])
        cursor.execute("SELECT gif_file_id, caption, likes, dislikes FROM gifs WHERE id = ?", (gif_id,))
        gif = cursor.fetchone()
        if not gif:
            safe_api_call(bot.send_message,
                          chat_id=message.chat.id,
                          text="⚠️ این رأی‌گیری حذف شده یا موجود نیست.")
            return
        gif_file_id, caption, likes, dislikes = gif
        markup = telebot.types.InlineKeyboardMarkup()
        markup.row(
            telebot.types.InlineKeyboardButton("👍 لایک", callback_data=f"like_{gif_id}"),
            telebot.types.InlineKeyboardButton("👎 دیسلایک", callback_data=f"dislike_{gif_id}")
        )
        safe_api_call(bot.send_animation,
                      chat_id=message.chat.id,
                      animation=gif_file_id,
                      caption=f"رای خود را به این گیف ثبت کنید:\n{caption}\n👍 {likes} | 👎 {dislikes}",
                      reply_markup=markup)
        return

    if is_admin(message.from_user.id):
        text = "سلام ادمین! 👑\nمی‌تونی گیف آپلود کنی و رأی‌گیری بذاری.\nبرای فعال کردن ری‌اکشن ❤️ روی پیام‌های کانال از /react_all استفاده کن."
    else:
        text = "سلام! 😊\nبه ربات گیف خوش آمدید.\nمی‌توانید به گیف‌های آپلود شده رأی بدهید 👍👎"
    safe_api_call(bot.reply_to, message, text)

# دریافت گیف
@bot.message_handler(content_types=['animation'])
def handle_gif(message):
    if not is_admin(message.from_user.id):
        return
    gif_file_id = message.animation.file_id
    user_states[message.from_user.id] = {'gif_file_id': gif_file_id, 'step': 'caption'}
    safe_api_call(bot.reply_to, message, "گیف دریافت شد. حالا کپشن را وارد کنید:")

# دریافت کپشن
@bot.message_handler(func=lambda m: m.from_user.id in user_states and user_states[m.from_user.id]['step'] == 'caption')
def handle_caption(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        del user_states[user_id]
        return

    gif_file_id = user_states[user_id]['gif_file_id']
    caption = message.text
    user_states[user_id]['caption'] = caption
    user_states[user_id]['step'] = 'scheduled_time'
    safe_api_call(bot.reply_to, message, "حالا زمان ارسال گیف را وارد کنید به فرمت ساعت:دقیقه (مثلاً 14:30):")

# دریافت زمان ارسال گیف
@bot.message_handler(func=lambda m: m.from_user.id in user_states and user_states[m.from_user.id]['step'] == 'scheduled_time')
def handle_scheduled_time(message):
    user_id = message.from_user.id
    if not is_admin(user_id):
        del user_states[user_id]
        return

    try:
        time_str = message.text.strip()
        send_hour, send_minute = map(int, time_str.split(":"))
        now = datetime.now()
        scheduled_time = now.replace(hour=send_hour, minute=send_minute, second=0, microsecond=0)
        if scheduled_time < now:
            scheduled_time += timedelta(days=1)
    except:
        safe_api_call(bot.reply_to, message, "فرمت زمان اشتباه است. لطفاً دوباره به شکل ساعت:دقیقه وارد کنید (مثلاً 14:30).")
        return

    caption = user_states[user_id]['caption']
    gif_file_id = user_states[user_id]['gif_file_id']

    cursor.execute('''
    INSERT INTO gifs (gif_file_id, caption, scheduled_time, user_id) VALUES (?, ?, ?, ?)
    ''', (gif_file_id, caption, scheduled_time, user_id))
    conn.commit()
    gif_id = cursor.lastrowid

    markup = telebot.types.InlineKeyboardMarkup()
    markup.row(
        telebot.types.InlineKeyboardButton("👍 لایک", callback_data=f"like_{gif_id}"),
        telebot.types.InlineKeyboardButton("👎 دیسلایک", callback_data=f"dislike_{gif_id}")
    )
    markup.row(telebot.types.InlineKeyboardButton("🗑 حذف رأی‌گیری", callback_data=f"delete_{gif_id}"))

    poll_message = safe_api_call(bot.send_animation,
                                 chat_id=message.chat.id,
                                 animation=gif_file_id,
                                 caption=f"رای خود را به این گیف ثبت کنید:\n{caption}\n👍 0 | 👎 0",
                                 reply_markup=markup)
    cursor.execute('UPDATE gifs SET poll_message_id = ? WHERE id = ?', (poll_message.message_id, gif_id))
    conn.commit()

    vote_link = f"https://t.me/{BOT_USERNAME}?start=vote_{gif_id}"
    safe_api_call(bot.send_message, message.chat.id,
                  f"گیف جدید آپلود شد! الان میتونی بری رای بدی:\n{vote_link}")

    del user_states[user_id]

# مدیریت رأی‌ها
@bot.callback_query_handler(func=lambda call: True)
def handle_vote(call):
    data = call.data
    user_id = call.from_user.id
    if data.startswith('like_') or data.startswith('dislike_'):
        gif_id = int(data.split('_')[1])
        new_vote_type = 'like' if data.startswith('like_') else 'dislike'
        cursor.execute('SELECT user_id FROM gifs WHERE id = ?', (gif_id,))
        gif_data = cursor.fetchone()
        if not gif_data:
            return
        cursor.execute('SELECT vote_type FROM votes WHERE user_id = ? AND gif_id = ?', (user_id, gif_id))
        existing_vote = cursor.fetchone()
        if existing_vote:
            old_vote_type = existing_vote[0]
            if old_vote_type == new_vote_type:
                return
            if old_vote_type == 'like':
                cursor.execute('UPDATE gifs SET likes = likes - 1 WHERE id = ?', (gif_id,))
            else:
                cursor.execute('UPDATE gifs SET dislikes = dislikes - 1 WHERE id = ?', (gif_id,))
        if new_vote_type == 'like':
            cursor.execute('UPDATE gifs SET likes = likes + 1 WHERE id = ?', (gif_id,))
        else:
            cursor.execute('UPDATE gifs SET dislikes = dislikes + 1 WHERE id = ?', (gif_id,))
        cursor.execute('''
        INSERT OR REPLACE INTO votes (user_id, gif_id, vote_type) VALUES (?, ?, ?)
        ''', (user_id, gif_id, new_vote_type))
        conn.commit()
        cursor.execute('SELECT likes, dislikes, caption FROM gifs WHERE id = ?', (gif_id,))
        gif_data = cursor.fetchone()
        if gif_data:
            likes, dislikes, caption = gif_data
            safe_api_call(bot.edit_message_caption,
                          chat_id=call.message.chat.id,
                          message_id=call.message.message_id,
                          caption=f"رای خود را به این گیف ثبت کنید:\n{caption}\n👍 {likes} | 👎 {dislikes}",
                          reply_markup=call.message.reply_markup)
    elif data.startswith('delete_'):
        if not is_admin(user_id):
            return
        gif_id = int(data.split('_')[1])
        cursor.execute('DELETE FROM gifs WHERE id = ?', (gif_id,))
        cursor.execute('DELETE FROM votes WHERE gif_id = ?', (gif_id,))
        conn.commit()
        safe_api_call(bot.edit_message_caption,
                      chat_id=call.message.chat.id,
                      message_id=call.message.message_id,
                      caption="این رأی‌گیری توسط ادمین حذف شد!",
                      reply_markup=None)

# بررسی زمان‌بندی و ارسال گیف
def check_and_send_gifs():
    now = datetime.now()
    cursor.execute('SELECT * FROM gifs WHERE scheduled_time <= ? AND approved = FALSE AND user_id IN (SELECT user_id FROM admins)', (now,))
    gifs = cursor.fetchall()
    for gif in gifs:
        gif_id, gif_file_id, caption, _, likes, dislikes, _, _, _ = gif

        # اگر تعداد دیسلایک‌ها بیشتر بود، رأی‌گیری حذف شود و گیف آپلود نشود
        if dislikes > likes:
            cursor.execute('DELETE FROM gifs WHERE id = ?', (gif_id,))
            cursor.execute('DELETE FROM votes WHERE gif_id = ?', (gif_id,))
            conn.commit()
            print(f"❌ GIF ID {gif_id} حذف شد چون دیسلایک‌ها بیشتر بودند.")
            continue

        # ارسال گیف به کانال
        safe_api_call(bot.send_animation, CHANNEL_ID, gif_file_id, caption=caption)
        cursor.execute('UPDATE gifs SET approved = TRUE WHERE id = ?', (gif_id,))
        conn.commit()
        print(f"✅ GIF ID {gif_id} با موفقیت ارسال شد به کانال!")


schedule.every(1).minutes.do(check_and_send_gifs)

def run_scheduler():
    print("✅ Scheduler ربات شروع شد و آماده بررسی گیف‌هاست.")
    while True:
        schedule.run_pending()
        time.sleep(1)

if __name__ == '__main__':
    threading.Thread(target=run_scheduler, daemon=True).start()
    bot.polling()
