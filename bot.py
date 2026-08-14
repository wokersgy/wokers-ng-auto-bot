import os
import json
import time
import threading
import logging
import traceback
from datetime import datetime

import requests
from flask import Flask, request, jsonify
from google import genai
from google.genai import types


# =========================================================
# WOKERS NG AI TELEGRAM BOT
# =========================================================

BOT_TOKEN = os.getenv("BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "CHANGE_THIS_PASSWORD").strip()

CHANNEL_ID = os.getenv("CHANNEL_ID", "@wokersng").strip()
PORT = int(os.getenv("PORT", "10000"))

MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

CONFIG_FILE = "bot_config.json"

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

app = Flask(__name__)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("wokers-ng")


# =========================================================
# STATE
# =========================================================

config_lock = threading.Lock()

DEFAULT_CONFIG = {
    "auto_enabled": False,
    "interval_minutes": 120,
    "topic": (
        "WOKERS NG, Nigerian jobs, digital skills, technology, "
        "education, freelancing, opportunities and useful updates"
    ),
    "next_post_at": 0,
    "last_post_at": 0
}

admin_sessions = {}
pending_admin_actions = {}

error_counter = 0
error_lock = threading.Lock()

gemini_cooldown_until = 0
gemini_lock = threading.Lock()


# =========================================================
# CONFIG
# =========================================================

def load_config():
    try:
        if not os.path.exists(CONFIG_FILE):
            save_config(DEFAULT_CONFIG.copy())
            return DEFAULT_CONFIG.copy()

        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)

        result = DEFAULT_CONFIG.copy()
        result.update(data)
        return result

    except Exception:
        logger.exception("Could not load config")
        return DEFAULT_CONFIG.copy()


def save_config(data):
    try:
        tmp = CONFIG_FILE + ".tmp"

        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

        os.replace(tmp, CONFIG_FILE)

    except Exception:
        logger.exception("Could not save config")


config = load_config()


# =========================================================
# TELEGRAM
# =========================================================

def telegram(method, payload=None, timeout=30):
    url = f"{TELEGRAM_API}/{method}"

    try:
        response = requests.post(
            url,
            json=payload or {},
            timeout=timeout
        )

        try:
            data = response.json()
        except Exception:
            data = {
                "ok": False,
                "description": response.text
            }

        if not response.ok or not data.get("ok"):
            raise RuntimeError(
                f"Telegram API error: {data}"
            )

        return data

    except Exception:
        raise


def send_message(chat_id, text, reply_markup=None):
    """
    Telegram max message size is around 4096 chars.
    Split safely if necessary.
    """

    if not text:
        text = "Babu rubutu."

    chunks = split_text(text, 3900)

    results = []

    for index, chunk in enumerate(chunks):

        markup = reply_markup if index == len(chunks) - 1 else None

        results.append(
            telegram(
                "sendMessage",
                {
                    "chat_id": chat_id,
                    "text": chunk,
                    "parse_mode": "HTML",
                    "disable_web_page_preview": False,
                    **(
                        {"reply_markup": markup}
                        if markup
                        else {}
                    )
                }
            )
        )

    return results


def send_channel_post(text):
    return send_message(CHANNEL_ID, text)


def split_text(text, max_length=3900):
    """
    Split long text without destroying words where possible.
    No word-count restriction.
    """

    if len(text) <= max_length:
        return [text]

    chunks = []
    remaining = text

    while len(remaining) > max_length:

        cut = remaining.rfind("\n", 0, max_length)

        if cut < 1000:
            cut = remaining.rfind(" ", 0, max_length)

        if cut < 1000:
            cut = max_length

        chunks.append(remaining[:cut].strip())
        remaining = remaining[cut:].strip()

    if remaining:
        chunks.append(remaining)

    return chunks


# =========================================================
# KEYBOARDS
# =========================================================

def admin_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "🤖 Generate Preview",
                    "callback_data": "admin_generate"
                },
                {
                    "text": "📤 Post Text",
                    "callback_data": "admin_post"
                }
            ],
            [
                {
                    "text": "🔄 Generate Again",
                    "callback_data": "admin_again"
                }
            ],
            [
                {
                    "text": "🟢 Auto ON",
                    "callback_data": "auto_on"
                },
                {
                    "text": "🔴 Auto OFF",
                    "callback_data": "auto_off"
                }
            ],
            [
                {
                    "text": "⏱ Set Time",
                    "callback_data": "set_time"
                },
                {
                    "text": "📝 Set Topic",
                    "callback_data": "set_topic"
                }
            ],
            [
                {
                    "text": "📊 Status",
                    "callback_data": "admin_status"
                }
            ]
        ]
    }


def time_keyboard():
    return {
        "inline_keyboard": [
            [
                {
                    "text": "15 min",
                    "callback_data": "time_15"
                },
                {
                    "text": "30 min",
                    "callback_data": "time_30"
                }
            ],
            [
                {
                    "text": "1 hour",
                    "callback_data": "time_60"
                },
                {
                    "text": "2 hours",
                    "callback_data": "time_120"
                }
            ],
            [
                {
                    "text": "6 hours",
                    "callback_data": "time_360"
                },
                {
                    "text": "12 hours",
                    "callback_data": "time_720"
                }
            ],
            [
                {
                    "text": "24 hours",
                    "callback_data": "time_1440"
                }
            ],
            [
                {
                    "text": "✏️ Custom minutes",
                    "callback_data": "time_custom"
                }
            ],
            [
                {
                    "text": "⬅️ Admin",
                    "callback_data": "admin_home"
                }
            ]
        ]
    }


# =========================================================
# ADMIN AUTH
# =========================================================

def is_admin(user_id):
    return bool(admin_sessions.get(str(user_id), False))


def start_admin_login(chat_id):
    pending_admin_actions[str(chat_id)] = "password"

    send_message(
        chat_id,
        "🔐 <b>WOKERS NG ADMIN</b>\n\n"
        "Rubuta admin password:"
    )


def admin_panel(chat_id):
    status = get_status_text()

    send_message(
        chat_id,
        "🛠 <b>WOKERS NG ADMIN PANEL</b>\n\n" + status,
        admin_keyboard()
    )


# =========================================================
# STATUS
# =========================================================

def get_status_text():
    with config_lock:
        c = config.copy()

    auto = "🟢 ON" if c["auto_enabled"] else "🔴 OFF"

    interval = c["interval_minutes"]

    if interval < 60:
        interval_text = f"{interval} minutes"
    elif interval % 60 == 0:
        interval_text = f"{interval // 60} hours"
    else:
        interval_text = f"{interval} minutes"

    next_time = c.get("next_post_at", 0)

    if next_time:
        next_text = datetime.fromtimestamp(next_time).strftime(
            "%Y-%m-%d %H:%M:%S"
        )
    else:
        next_text = "Not scheduled"

    topic = c.get("topic", "")

    return (
        f"⚙️ <b>Auto Post:</b> {auto}\n"
        f"⏱ <b>Interval:</b> {interval_text}\n"
        f"📅 <b>Next:</b> {next_text}\n\n"
        f"📝 <b>Topic:</b>\n{escape_html(topic)}"
    )


# =========================================================
# HTML ESCAPE
# =========================================================

def escape_html(text):
    if not text:
        return ""

    return (
        str(text)
        .replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
    )


# =========================================================
# GEMINI
# =========================================================

def get_gemini_client():
    if not GEMINI_API_KEY:
        raise RuntimeError(
            "GEMINI_API_KEY is missing."
        )

    return genai.Client(
        api_key=GEMINI_API_KEY
    )


def is_quota_error(error):
    text = str(error).upper()

    return (
        "429" in text
        or "RESOURCE_EXHAUSTED" in text
        or "QUOTA" in text
    )


def get_retry_seconds(error):
    text = str(error)

    # Default cooldown.
    retry_seconds = 60

    # Try to read RetryInfo seconds from error text.
    import re

    matches = re.findall(
        r"retryDelay.*?(\d+)s",
        text,
        flags=re.IGNORECASE
    )

    if matches:
        try:
            retry_seconds = max(
                30,
                int(matches[0])
            )
        except Exception:
            pass

    # Do not allow absurdly long cooldown from parsing.
    retry_seconds = min(
        retry_seconds,
        3600
    )

    return retry_seconds


def gemini_generate(prompt):
    global gemini_cooldown_until

    now = time.time()

    with gemini_lock:

        if now < gemini_cooldown_until:

            remaining = int(
                gemini_cooldown_until - now
            )

            raise RuntimeError(
                f"Gemini quota cooldown active. "
                f"Retry in about {remaining} seconds."
            )

    client = get_gemini_client()

    try:

        response = client.models.generate_content(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=0.9
            )
        )

        text = getattr(response, "text", None)

        if not text:
            raise RuntimeError(
                "Gemini returned an empty response."
            )

        return text.strip()

    except Exception as error:

        if is_quota_error(error):

            retry_seconds = get_retry_seconds(error)

            with gemini_lock:
                gemini_cooldown_until = (
                    time.time() + retry_seconds
                )

            raise RuntimeError(
                f"Gemini quota exhausted. "
                f"Retry after {retry_seconds} seconds."
            ) from error

        raise


# =========================================================
# POST GENERATION
# =========================================================

def build_post_prompt(topic):
    return f"""
You are the official WOKERS NG Telegram content assistant.

Create a useful, natural Telegram post.

Main topic:
{topic}

Rules:
- Write naturally like a Nigerian human content creator.
- Do not mention AI.
- Do not say you are an AI.
- Do not use robotic phrases.
- Make the information useful and clear.
- You may include emojis where appropriate.
- Do not create images.
- Text only.
- Do not invent dangerous or obviously false claims.
- If the topic asks for current information but you cannot verify it,
  clearly avoid pretending that unverified information is confirmed.
- Do not add unnecessary explanations about these instructions.

Return ONLY the Telegram post.
"""


def generate_post(topic=None):
    if not topic:
        with config_lock:
            topic = config["topic"]

    prompt = build_post_prompt(topic)

    return gemini_generate(prompt)


# =========================================================
# ERROR LOG
# =========================================================

def get_error_number():
    global error_counter

    with error_lock:
        error_counter += 1
        return error_counter


def log_error(source, error, notify_admin=True):
    number = get_error_number()

    logger.error(
        "ERROR #%s | %s | %s",
        number,
        source,
        error
    )

    traceback.print_exc()

    message = (
        f"🐞 <b>WOKERS NG ERROR LOG</b> #{number}\n\n"
        f"<b>Time:</b> "
        f"{datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n"
        f"<b>Source:</b> {escape_html(source)}\n"
        f"<b>Error:</b>\n"
        f"<code>{escape_html(str(error))[:3000]}</code>"
    )

    if notify_admin:

        for user_id, status in list(
            admin_sessions.items()
        ):

            if status:

                try:
                    send_message(
                        int(user_id),
                        message
                    )
                except Exception:
                    logger.exception(
                        "Could not send error to admin."
                    )

    return number


# =========================================================
# POSTING
# =========================================================

def create_and_post(topic=None, notify=True):

    try:

        post = generate_post(topic)

        send_channel_post(post)

        with config_lock:

            config["last_post_at"] = time.time()

            if config["auto_enabled"]:
                config["next_post_at"] = (
                    time.time()
                    + config["interval_minutes"] * 60
                )

            save_config(config)

        logger.info(
            "Post successfully sent to %s",
            CHANNEL_ID
        )

        return True, post

    except Exception as error:

        log_error(
            "create_and_post",
            error,
            notify_admin=notify
        )

        return False, str(error)


# =========================================================
# AUTOMATIC WORKER
# =========================================================

def automatic_worker():

    logger.info(
        "Automatic worker started."
    )

    while True:

        try:

            with config_lock:
                enabled = config["auto_enabled"]
                next_post = config.get(
                    "next_post_at",
                    0
                )

            if enabled:

                now = time.time()

                if next_post <= now:

                    logger.info(
                        "Automatic post is due."
                    )

                    success, result = create_and_post(
                        notify=True
                    )

                    if not success:

                        # Prevent fast repeated attempts.
                        with config_lock:

                            config["next_post_at"] = (
                                time.time() + 300
                            )

                            save_config(config)

                    else:

                        logger.info(
                            "Automatic post completed."
                        )

                else:

                    time.sleep(
                        min(
                            max(
                                5,
                                int(next_post - now)
                            ),
                            30
                        )
                    )

            else:

                time.sleep(10)

        except Exception as error:

            log_error(
                "automatic_worker",
                error,
                notify_admin=True
            )

            # Most important:
            # worker NEVER dies.
            time.sleep(30)


# =========================================================
# CALLBACKS
# =========================================================

def answer_callback(callback_id):
    try:
        telegram(
            "answerCallbackQuery",
            {
                "callback_query_id": callback_id
            }
        )
    except Exception:
        pass


def handle_callback(callback):

    callback_id = callback.get("id")
    data = callback.get("data", "")

    message = callback.get("message", {})
    chat = message.get("chat", {})
    chat_id = chat.get("id")

    user = callback.get("from", {})
    user_id = user.get("id")

    answer_callback(callback_id)

    if not is_admin(user_id):

        try:
            send_message(
                chat_id,
                "🔐 Wannan sashe na admin ne kawai."
            )
        except Exception:
            pass

        return

    # -----------------------------------------------------
    # ADMIN HOME
    # -----------------------------------------------------

    if data == "admin_home":

        admin_panel(chat_id)
        return

    # -----------------------------------------------------
    # GENERATE PREVIEW
    # -----------------------------------------------------

    if data in (
        "admin_generate",
        "admin_again"
    ):

        send_message(
            chat_id,
            "⏳ Ina generate post..."
        )

        try:

            post = generate_post()

            send_message(
                chat_id,
                "👀 <b>POST PREVIEW</b>\n\n"
                + escape_html(post),
                {
                    "inline_keyboard": [
                        [
                            {
                                "text": "📤 Post Now",
                                "callback_data": "post_preview"
                            }
                        ],
                        [
                            {
                                "text": "🔄 Generate Again",
                                "callback_data": "admin_again"
                            }
                        ],
                        [
                            {
                                "text": "⬅️ Admin",
                                "callback_data": "admin_home"
                            }
                        ]
                    ]
                }
            )

        except Exception as error:

            log_error(
                "manual_generate",
                error,
                notify_admin=False
            )

            send_message(
                chat_id,
                "⚠️ Ba a iya Generate Post yanzu.\n\n"
                "Bot ɗin yana ci gaba da aiki."
            )

        return

    # -----------------------------------------------------
    # POST PREVIEW
    # -----------------------------------------------------

    if data == "post_preview":

        send_message(
            chat_id,
            "⏳ Ana ƙoƙarin posting..."
        )

        success, result = create_and_post(
            notify=False
        )

        if success:

            send_message(
                chat_id,
                "✅ An yi posting successfully."
            )

        else:

            send_message(
                chat_id,
                "⚠️ An samu matsala wajen posting.\n"
                "Bot ɗin yana ci gaba da aiki."
            )

        return

    # -----------------------------------------------------
    # POST TEXT DIRECT
    # -----------------------------------------------------

    if data == "admin_post":

        send_message(
            chat_id,
            "⏳ Ina generate post sannan in aika channel..."
        )

        success, result = create_and_post(
            notify=False
        )

        if success:

            send_message(
                chat_id,
                "✅ An aika post zuwa channel."
            )

        else:

            send_message(
                chat_id,
                "⚠️ Posting ya kasa.\n"
                "An rubuta error a log."
            )

        return

    # -----------------------------------------------------
    # AUTO ON
    # -----------------------------------------------------

    if data == "auto_on":

        with config_lock:

            config["auto_enabled"] = True

            config["next_post_at"] = (
                time.time()
                + config["interval_minutes"] * 60
            )

            save_config(config)

        send_message(
            chat_id,
            "🟢 <b>Automatic Post ON</b>\n\n"
            + get_status_text(),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # AUTO OFF
    # -----------------------------------------------------

    if data == "auto_off":

        with config_lock:

            config["auto_enabled"] = False
            config["next_post_at"] = 0

            save_config(config)

        send_message(
            chat_id,
            "🔴 <b>Automatic Post OFF</b>",
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # TIME MENU
    # -----------------------------------------------------

    if data == "set_time":

        send_message(
            chat_id,
            "⏱ <b>Automatic Post Interval</b>\n\n"
            "Zaɓi lokacin da kake so:",
            time_keyboard()
        )

        return

    # -----------------------------------------------------
    # CUSTOM TIME
    # -----------------------------------------------------

    if data == "time_custom":

        pending_admin_actions[
            str(chat_id)
        ] = "custom_time"

        send_message(
            chat_id,
            "⏱ Rubuta interval a minutes.\n\n"
            "Misali:\n"
            "<code>90</code>\n\n"
            "Wannan zai yi post duk mintuna 90."
        )

        return

    # -----------------------------------------------------
    # PRESET TIME
    # -----------------------------------------------------

    if data.startswith("time_"):

        value = data.replace(
            "time_",
            ""
        )

        try:

            minutes = int(value)

            if minutes < 1:
                raise ValueError()

            with config_lock:

                config["interval_minutes"] = minutes

                if config["auto_enabled"]:

                    config["next_post_at"] = (
                        time.time()
                        + minutes * 60
                    )

                save_config(config)

            send_message(
                chat_id,
                "✅ <b>Automatic time updated.</b>\n\n"
                + get_status_text(),
                admin_keyboard()
            )

        except Exception:

            send_message(
                chat_id,
                "❌ Lokacin bai yi daidai ba."
            )

        return

    # -----------------------------------------------------
    # TOPIC
    # -----------------------------------------------------

    if data == "set_topic":

        pending_admin_actions[
            str(chat_id)
        ] = "topic"

        with config_lock:
            current_topic = config["topic"]

        send_message(
            chat_id,
            "📝 <b>Custom Post Topic</b>\n\n"
            "Rubuta abin da kake so posts su fi mayar da hankali a kai.\n\n"
            "<b>Misali:</b>\n"
            "AI tools, scholarships, Nigerian jobs, "
            "programming da digital skills.\n\n"
            f"<b>Current:</b>\n"
            f"{escape_html(current_topic)}"
        )

        return

    # -----------------------------------------------------
    # STATUS
    # -----------------------------------------------------

    if data == "admin_status":

        send_message(
            chat_id,
            "📊 <b>WOKERS NG STATUS</b>\n\n"
            + get_status_text(),
            admin_keyboard()
        )

        return


# =========================================================
# TEXT MESSAGE HANDLER
# =========================================================

def handle_text_message(message):

    chat = message.get("chat", {})
    chat_id = chat.get("id")

    user = message.get("from", {})
    user_id = user.get("id")

    text = (message.get("text") or "").strip()

    if not text:
        return

    key = str(chat_id)

    # -----------------------------------------------------
    # ADMIN PASSWORD
    # -----------------------------------------------------

    if pending_admin_actions.get(key) == "password":

        pending_admin_actions.pop(key, None)

        if text == ADMIN_PASSWORD:

            admin_sessions[str(user_id)] = True

            send_message(
                chat_id,
                "✅ <b>Admin access granted.</b>\n\n"
                "Welcome Admin.",
                admin_keyboard()
            )

        else:

            send_message(
                chat_id,
                "❌ Incorrect admin password."
            )

        return

    # -----------------------------------------------------
    # CUSTOM TIME
    # -----------------------------------------------------

    if (
        is_admin(user_id)
        and pending_admin_actions.get(key)
        == "custom_time"
    ):

        pending_admin_actions.pop(key, None)

        try:

            minutes = int(text)

            if minutes < 1:
                raise ValueError()

            if minutes > 43200:
                raise ValueError()

            with config_lock:

                config["interval_minutes"] = minutes

                if config["auto_enabled"]:

                    config["next_post_at"] = (
                        time.time()
                        + minutes * 60
                    )

                save_config(config)

            send_message(
                chat_id,
                "✅ <b>Custom interval saved.</b>\n\n"
                + get_status_text(),
                admin_keyboard()
            )

        except Exception:

            send_message(
                chat_id,
                "❌ Ka rubuta number tsakanin "
                "1 da 43200 minutes."
            )

        return

    # -----------------------------------------------------
    # CUSTOM TOPIC
    # -----------------------------------------------------

    if (
        is_admin(user_id)
        and pending_admin_actions.get(key)
        == "topic"
    ):

        pending_admin_actions.pop(key, None)

        if len(text) < 2:

            send_message(
                chat_id,
                "❌ Topic ɗin ya yi gajere."
            )

            return

        with config_lock:

            config["topic"] = text
            save_config(config)

        send_message(
            chat_id,
            "✅ <b>Post topic updated.</b>\n\n"
            + escape_html(text),
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # /admin
    # -----------------------------------------------------

    if text.lower() == "/admin":

        start_admin_login(chat_id)
        return

    # -----------------------------------------------------
    # /logout
    # -----------------------------------------------------

    if text.lower() == "/logout":

        admin_sessions.pop(
            str(user_id),
            None
        )

        send_message(
            chat_id,
            "🔒 Admin session closed."
        )

        return

    # -----------------------------------------------------
    # /start
    # -----------------------------------------------------

    if text.lower().startswith("/start"):

        send_message(
            chat_id,
            "👋 <b>Barka da zuwa WOKERS NG!</b>\n\n"
            "Ni ne WOKERS NG Assistant.\n\n"
            "Tambaye ni game da WOKERS NG, "
            "ilimi, digital skills da sauran "
            "abubuwan da suka shafi ilimi da fasaha."
        )

        return

    # -----------------------------------------------------
    # ADMIN CHAT
    # -----------------------------------------------------

    if is_admin(user_id):

        send_message(
            chat_id,
            "🛠 Kana cikin Admin Panel.\n\n"
            "Yi amfani da buttons ɗin da ke sama.",
            admin_keyboard()
        )

        return

    # -----------------------------------------------------
    # NORMAL USER AI
    # -----------------------------------------------------

    try:

        prompt = f"""
You are WOKERS NG Telegram Assistant.

Answer the user's question.

Allowed subjects:
- WOKERS NG
- education
- technology
- programming
- digital skills
- useful learning information

User question:
{text}

Write a helpful, natural answer in Hausa where appropriate.
Do not mention AI unnecessarily.
"""

        answer = gemini_generate(prompt)

        send_message(
            chat_id,
            escape_html(answer)
        )

    except Exception as error:

        log_error(
            "user_ai",
            error,
            notify_admin=True
        )

        if is_quota_error(error):

            send_message(
                chat_id,
                "⚠️ Assistant yana samun cikas na ɗan lokaci. "
                "A sake gwadawa daga baya."
            )

        else:

            send_message(
                chat_id,
                "⚠️ An samu matsala na ɗan lokaci. "
                "Bot ɗin yana ci gaba da aiki."
            )


# =========================================================
# UPDATE HANDLER
# =========================================================

def process_update(update):

    try:

        if "callback_query" in update:

            handle_callback(
                update["callback_query"]
            )

            return

        message = update.get("message")

        if message:

            handle_text_message(message)

    except Exception as error:

        log_error(
            "process_update",
            error,
            notify_admin=True
        )


# =========================================================
# WEBHOOK
# =========================================================

@app.route("/", methods=["GET", "HEAD"])
def home():

    return jsonify({
        "status": "online",
        "bot": "WOKERS NG AI Bot",
        "automatic_post": config["auto_enabled"],
        "interval_minutes": config["interval_minutes"]
    })


@app.route("/health", methods=["GET"])
def health():

    return jsonify({
        "ok": True,
        "service": "wokers-ng-auto-bot"
    })


@app.route("/telegram-webhook", methods=["POST"])
def telegram_webhook():

    try:

        update = request.get_json(
            silent=True
        )

        if not update:
            return jsonify({
                "ok": True
            })

        # Process quickly.
        threading.Thread(
            target=process_update,
            args=(update,),
            daemon=True
        ).start()

        return jsonify({
            "ok": True
        })

    except Exception as error:

        log_error(
            "telegram_webhook",
            error,
            notify_admin=True
        )

        return jsonify({
            "ok": True
        })


# =========================================================
# SET WEBHOOK
# =========================================================

def setup_webhook():

    render_url = os.getenv(
        "RENDER_EXTERNAL_URL",
        ""
    ).strip()

    if not render_url:

        logger.warning(
            "RENDER_EXTERNAL_URL not available."
        )

        return False

    webhook_url = (
        render_url.rstrip("/")
        + "/telegram-webhook"
    )

    try:

        result = telegram(
            "setWebhook",
            {
                "url": webhook_url,
                "drop_pending_updates": True,
                "allowed_updates": [
                    "message",
                    "callback_query"
                ]
            }
        )

        logger.info(
            "Webhook configured: %s",
            result.get("ok")
        )

        return True

    except Exception as error:

        log_error(
            "setup_webhook",
            error,
            notify_admin=False
        )

        return False


# =========================================================
# STARTUP
# =========================================================

def validate_environment():

    missing = []

    if not BOT_TOKEN:
        missing.append("BOT_TOKEN")

    if not GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")

    if not ADMIN_PASSWORD:
        missing.append("ADMIN_PASSWORD")

    if missing:

        raise RuntimeError(
            "Missing environment variables: "
            + ", ".join(missing)
        )


def main():

    logger.info("=" * 50)
    logger.info("WOKERS NG AI BOT STARTING")
    logger.info("Model: %s", MODEL)
    logger.info("Channel: %s", CHANNEL_ID)
    logger.info("Image generation: DISABLED")
    logger.info("Word limit: DISABLED")
    logger.info("=" * 50)

    validate_environment()

    setup_webhook()

    worker = threading.Thread(
        target=automatic_worker,
        daemon=True
    )

    worker.start()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


if __name__ == "__main__":

    try:
        main()

    except Exception as error:

        # Log fatal startup error.
        # This only happens if environment/startup
        # is fundamentally broken.
        logger.exception(
            "Fatal startup error: %s",
            error
        )

        # Do not hide the error from Render logs.
        raise