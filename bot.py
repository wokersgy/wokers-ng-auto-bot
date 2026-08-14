import os
import re
import asyncio
import logging
import threading
from datetime import datetime, timedelta

import httpx
from flask import Flask, request, jsonify

from google import genai
from google.genai import types
from google.genai.errors import ClientError


# ============================================================
# WOKERS NG TELEGRAM AI BOT
# ============================================================
#
# USER:
#   /start
#   Ask WOKERS NG / educational questions
#   5-20 words per answer
#
# ADMIN:
#   Admin Assistant -> password
#   Generate Post
#   Preview
#   Generate Again
#   Post Now
#   Automatic ON/OFF
#   Interval
#   Topic
#   Status
#
# CHANNEL POSTS:
#   20-50 words
#   Text only
#   No image generation
#
# ============================================================


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("wokers-ng-bot")


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
).strip()

ADMIN_PASSWORD = os.getenv(
    "ADMIN_PASSWORD",
    ""
).strip()

CHANNEL_USERNAME = os.getenv(
    "TELEGRAM_CHANNEL",
    "@wokersng"
).strip()

RENDER_EXTERNAL_URL = os.getenv(
    "RENDER_EXTERNAL_URL",
    ""
).strip()

WEBHOOK_SECRET = os.getenv(
    "WEBHOOK_SECRET",
    ""
).strip()

GEMINI_MODEL = os.getenv(
    "GEMINI_MODEL",
    "gemini-2.5-flash"
).strip()


# ============================================================
# REQUIRED ENVIRONMENT CHECK
# ============================================================

if not BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is missing."
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing."
    )

if not ADMIN_PASSWORD:
    raise RuntimeError(
        "ADMIN_PASSWORD is missing."
    )


# ============================================================
# GEMINI
# ============================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# TELEGRAM API
# ============================================================

TELEGRAM_API = (
    "https://api.telegram.org/"
    f"bot{BOT_TOKEN}"
)


# ============================================================
# STATE
# ============================================================

state = {

    "auto_enabled": False,

    "interval_minutes": 120,

    "topic": "Technology",

    "custom_topic": "",

    "search_enabled": True,

    "last_post": None,

    "next_post": None,

    "last_error": None

}


# ============================================================
# USERS
# ============================================================

# Runtime-only sessions.
#
# admin_authenticated:
#   True = admin currently authenticated
#
# waiting_password:
#   User is entering admin password
#
# waiting_custom_topic:
#   Admin is entering custom topic
#
# waiting_question:
#   User is asking assistant
#
users = {}


# ============================================================
# ADMIN PASSWORD ATTEMPTS
# ============================================================

MAX_PASSWORD_ATTEMPTS = 5

password_attempts = {}


# ============================================================
# TOPICS
# ============================================================

TOPICS = {

    "AI": "Artificial Intelligence and useful AI tools",

    "Technology": "Technology and digital trends",

    "Apps": "Mobile applications and useful apps",

    "Websites": "Useful websites and online services",

    "Programming": "Programming and software development",

    "Jobs": "Digital skills, jobs and career education",

    "Freelancing": "Freelancing and digital work skills",

    "Business": "Digital business and entrepreneurship",

    "Education": "Education and learning",

    "Cybersecurity": "Cybersecurity and online safety",

    "Phones": "Android, smartphones and mobile technology",

    "Internet": "Internet tips and digital services",

    "Productivity": "Productivity and digital habits",

    "News": "Technology news and recent developments"

}


# ============================================================
# INTERVALS
# ============================================================

INTERVALS = {

    5: "5 minutes",
    10: "10 minutes",
    30: "30 minutes",
    60: "1 hour",
    120: "2 hours",
    360: "6 hours",
    720: "12 hours",
    1440: "24 hours"

}


# ============================================================
# TELEGRAM REQUEST
# ============================================================

async def telegram_request(
    method,
    data=None
):

    url = (
        f"{TELEGRAM_API}/{method}"
    )

    try:

        async with httpx.AsyncClient(
            timeout=60
        ) as http:

            response = await http.post(
                url,
                data=data or {}
            )

        try:

            result = response.json()

        except Exception:

            raise RuntimeError(
                f"Telegram returned HTTP "
                f"{response.status_code}"
            )

        if not result.get("ok"):

            description = result.get(
                "description",
                "Telegram API error"
            )

            raise RuntimeError(
                description
            )

        return result

    except httpx.TimeoutException:

        raise RuntimeError(
            "Telegram request timeout."
        )

    except httpx.RequestError as error:

        raise RuntimeError(
            f"Telegram network error: {error}"
        )


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_message(
    chat_id,
    text,
    keyboard=None
):

    text = str(text).strip()

    if len(text) > 4096:

        text = text[:4080] + "..."

    data = {

        "chat_id": chat_id,

        "text": text,

        "disable_web_page_preview": "false"

    }

    if keyboard:

        import json

        data["reply_markup"] = json.dumps(
            keyboard,
            ensure_ascii=False
        )

    return await telegram_request(
        "sendMessage",
        data
    )


# ============================================================
# ERROR MESSAGE
# ============================================================

async def send_user_error(
    chat_id,
    public_message
):

    try:

        await send_message(
            chat_id,
            (
                "⚠️ *Matsala ta faru*\n\n"
                f"{public_message}\n\n"
                "Bot ɗin yana ci gaba da aiki."
            ),
            home_keyboard()
        )

    except Exception as error:

        logger.error(
            "Could not send error message: %s",
            error
        )


# ============================================================
# CALLBACK ANSWER
# ============================================================

async def answer_callback(
    callback_id,
    text=""
):

    data = {
        "callback_query_id": callback_id
    }

    if text:

        data["text"] = text

    try:

        return await telegram_request(
            "answerCallbackQuery",
            data
        )

    except Exception as error:

        logger.warning(
            "Callback error: %s",
            error
        )


# ============================================================
# WORD COUNT
# ============================================================

def word_count(text):

    return len(
        re.findall(
            r"\S+",
            str(text)
        )
    )


# ============================================================
# CLEAN AI TEXT
# ============================================================

def clean_ai_text(text):

    if not text:

        return ""

    text = str(text).strip()

    text = re.sub(
        r"^```(?:text|markdown)?",
        "",
        text,
        flags=re.IGNORECASE
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    text = text.strip()

    # Remove common AI introductions
    prefixes = [
        "Here is the post:",
        "Here is the answer:",
        "Ga amsar:",
        "Ga post:",
        "Sure,",
        "Sure.",
        "As an AI,"
    ]

    for prefix in prefixes:

        if text.lower().startswith(
            prefix.lower()
        ):

            text = text[
                len(prefix):
            ].strip()

    return text


# ============================================================
# LIMIT WORDS
# ============================================================

def limit_words(
    text,
    minimum,
    maximum
):

    text = clean_ai_text(
        text
    )

    words = re.findall(
        r"\S+",
        text
    )

    if len(words) <= maximum:

        return text

    result = " ".join(
        words[:maximum]
    )

    return result.rstrip(
        ".,!?;:"
    ) + "..."


# ============================================================
# HOME KEYBOARD
# ============================================================

def home_keyboard():

    return {

        "inline_keyboard": [

            [
                {
                    "text": "🤖 Ask Assistant",
                    "callback_data": "user_assistant"
                }
            ],

            [
                {
                    "text": "🛠 Admin Assistant",
                    "callback_data": "admin_login"
                }
            ]

        ]

    }


# ============================================================
# USER KEYBOARD
# ============================================================

def user_keyboard():

    return {

        "inline_keyboard": [

            [
                {
                    "text": "🤖 Ask Another",
                    "callback_data": "user_assistant"
                }
            ],

            [
                {
                    "text": "🏠 Home",
                    "callback_data": "home"
                }
            ]

        ]

    }


# ============================================================
# ADMIN KEYBOARD
# ============================================================

def admin_keyboard():

    auto = state[
        "auto_enabled"
    ]

    return {

        "inline_keyboard": [

            [
                {
                    "text": "🚀 Generate Post",
                    "callback_data": "admin_generate"
                }
            ],

            [
                {
                    "text": "📚 Choose Topic",
                    "callback_data": "admin_topics"
                },

                {
                    "text": "👀 Preview",
                    "callback_data": "admin_preview"
                }

            ],

            [
                {
                    "text": "📢 Post Now",
                    "callback_data": "admin_post"
                },

                {
                    "text": "🔄 Generate Again",
                    "callback_data": "admin_again"
                }

            ],

            [
                {
                    "text": (
                        "🟢 Auto: ON"
                        if auto
                        else
                        "🔴 Auto: OFF"
                    ),
                    "callback_data": "admin_auto"
                },

                {
                    "text": "⏱ Interval",
                    "callback_data": "admin_interval"
                }

            ],

            [
                {
                    "text": "🔎 Search",
                    "callback_data": "admin_search"
                },

                {
                    "text": "📊 Status",
                    "callback_data": "admin_status"
                }

            ],

            [
                {
                    "text": "🚪 Logout",
                    "callback_data": "admin_logout"
                }

            ]

        ]

    }


# ============================================================
# TOPIC KEYBOARD
# ============================================================

def topic_keyboard():

    buttons = []

    topics = list(
        TOPICS.keys()
    )

    for i in range(
        0,
        len(topics),
        2
    ):

        row = []

        for topic in topics[
            i:i + 2
        ]:

            row.append({

                "text": topic,

                "callback_data":
                    "admin_topic:" + topic

            })

        buttons.append(
            row
        )

    buttons.append([

        {
            "text": "✍️ Custom Topic",
            "callback_data": "admin_custom_topic"
        }

    ])

    buttons.append([

        {
            "text": "⬅️ Admin",
            "callback_data": "admin_home"
        }

    ])

    return {
        "inline_keyboard": buttons
    }


# ============================================================
# INTERVAL KEYBOARD
# ============================================================

def interval_keyboard():

    rows = []

    values = list(
        INTERVALS.items()
    )

    for i in range(
        0,
        len(values),
        2
    ):

        row = []

        for minutes, label in values[
            i:i + 2
        ]:

            row.append({

                "text": label,

                "callback_data":
                    f"admin_interval:{minutes}"

            })

        rows.append(
            row
        )

    rows.append([

        {
            "text": "⬅️ Admin",
            "callback_data": "admin_home"
        }

    ])

    return {
        "inline_keyboard": rows
    }


# ============================================================
# PREVIEW KEYBOARD
# ============================================================

def preview_keyboard():

    return {

        "inline_keyboard": [

            [
                {
                    "text": "📢 Post Now",
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
                    "text": "❌ Cancel",
                    "callback_data": "admin_home"
                }
            ]

        ]

    }


# ============================================================
# ADMIN CHECK
# ============================================================

def is_admin(
    chat_id
):

    return users.get(
        chat_id,
        {}
    ).get(
        "admin",
        False
    )


# ============================================================
# USER START
# ============================================================

async def send_welcome(
    chat_id
):

    await send_message(
        chat_id,
        """
👋 *Barka da zuwa WOKERS NG*

🤖 Ni ne WOKERS NG Assistant.

Za ka iya tambayata abubuwan da suka shafi:

📚 Ilimi
💻 Technology
📱 Apps
🌐 Websites
🤖 AI
👨‍💻 Programming
💼 Digital skills

Amsata za ta kasance gajera kuma kai tsaye.

👇 Zaɓi:
""",
        home_keyboard()
    )


# ============================================================
# ADMIN LOGIN
# ============================================================

async def start_admin_login(
    chat_id
):

    users.setdefault(
        chat_id,
        {}
    )

    users[chat_id][
        "waiting_password"
    ] = True

    password_attempts[
        chat_id
    ] = 0

    await send_message(
        chat_id,
        """
🔐 *Admin Assistant*

Domin samun Admin Access,
rubuta Admin Password.

⚠️ Kada ka tura password ɗin
a wani group ko channel.
"""
    )


# ============================================================
# ADMIN PANEL
# ============================================================

async def send_admin_panel(
    chat_id
):

    await send_message(
        chat_id,
        f"""
🛠 *WOKERS NG ADMIN PANEL*

🔐 Access: GRANTED

📚 Topic:
{state["topic"]}

⏱ Interval:
{INTERVALS.get(
    state["interval_minutes"],
    "Custom"
)}

🤖 Automatic:
{"🟢 ON" if state["auto_enabled"] else "🔴 OFF"}

🔎 Web Search:
{"🟢 ON" if state["search_enabled"] else "🔴 OFF"}

📢 Channel:
{CHANNEL_USERNAME}
""",
        admin_keyboard()
    )


# ============================================================
# VERIFY ADMIN PASSWORD
# ============================================================

async def verify_admin_password(
    chat_id,
    password
):

    attempts = password_attempts.get(
        chat_id,
        0
    )

    if attempts >= MAX_PASSWORD_ATTEMPTS:

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "waiting_password"
        ] = False

        await send_message(
            chat_id,
            """
🔒 An kulle Admin Login na ɗan lokaci
saboda yawan password attempts.

Ka sake /start daga baya.
""",
            home_keyboard()
        )

        return

    if password == ADMIN_PASSWORD:

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "admin"
        ] = True

        users[chat_id][
            "waiting_password"
        ] = False

        password_attempts.pop(
            chat_id,
            None
        )

        await send_message(
            chat_id,
            """
✅ *Admin Access Granted*

Barka da zuwa Admin Panel.
""",
            admin_keyboard()
        )

    else:

        password_attempts[
            chat_id
        ] = attempts + 1

        remaining = (
            MAX_PASSWORD_ATTEMPTS
            - (
                attempts + 1
            )
        )

        await send_message(
            chat_id,
            (
                "❌ Password ba daidai ba.\n\n"
                f"Attempts remaining: {remaining}"
            )
        )


# ============================================================
# USER QUESTION PROMPT
# ============================================================

def user_question_prompt(
    question
):

    return f"""
You are WOKERS NG Assistant.

Answer this user question:

{question}

STRICT RULES:

1. Answer ONLY questions related to:
   - WOKERS NG
   - education
   - learning
   - technology education
   - programming education
   - AI education
   - useful digital skills
   - apps/websites for learning

2. If unrelated, politely say you only
   answer WOKERS NG or educational questions.

3. Answer in natural Nigerian Hausa.

4. Maximum 20 words.

5. Minimum useful answer where possible.

6. Be direct.

7. Do not make up facts.

8. Do not give long explanations.

9. Do not say:
   "As an AI..."

10. Return ONLY the answer.
"""


# ============================================================
# USER ASSISTANT
# ============================================================

async def answer_user_question(
    chat_id,
    question
):

    question = question.strip()

    if not question:

        await send_message(
            chat_id,
            "Rubuta tambayarka.",
            user_keyboard()
        )

        return

    # Keep user prompt reasonable
    question = question[:1000]

    try:

        response = await asyncio.to_thread(

            gemini.models.generate_content,

            model=GEMINI_MODEL,

            contents=user_question_prompt(
                question
            ),

            config=types.GenerateContentConfig(
                max_output_tokens=100
            )

        )

        answer = getattr(
            response,
            "text",
            ""
        )

        answer = limit_words(
            answer,
            5,
            20
        )

        if not answer:

            raise RuntimeError(
                "Empty Gemini response."
            )

        await send_message(
            chat_id,
            answer,
            user_keyboard()
        )

    except ClientError as error:

        logger.exception(
            "User assistant Gemini error"
        )

        if getattr(
            error,
            "code",
            None
        ) == 429:

            await send_user_error(
                chat_id,
                "Gemini quota ya cika. Ka sake gwadawa daga baya."
            )

        else:

            await send_user_error(
                chat_id,
                "Assistant ya samu matsala. Ka sake gwadawa."
            )

    except Exception as error:

        logger.exception(
            "User assistant error"
        )

        await send_user_error(
            chat_id,
            "An samu matsala wajen amsa tambayar."
        )


# ============================================================
# ADMIN POST PROMPT
# ============================================================

def admin_post_prompt():

    topic = state[
        "topic"
    ]

    if topic == "Custom":

        topic_description = state[
            "custom_topic"
        ]

    else:

        topic_description = TOPICS.get(
            topic,
            topic
        )

    search_instruction = ""

    if state[
        "search_enabled"
    ]:

        search_instruction = """
Use Google Search when current information
is necessary.

If you mention a website or source,
only use real URLs.

Never invent URLs.
"""

    else:

        search_instruction = """
Do not use web search.
"""

    return f"""
Create ONE Telegram post for WOKERS NG.

TOPIC:
{topic_description}

{search_instruction}

LANGUAGE:
Natural Nigerian Hausa.

POST LENGTH:
20 to 50 words ONLY.

STYLE:
- Useful
- Short
- Natural
- Professional
- Friendly
- Easy to read
- Use a few suitable emojis
- No long introduction
- No unnecessary explanation

Do not fabricate:
- News
- Statistics
- URLs
- Companies
- Jobs
- Salaries

Do not say:
"As an AI"
"Here is the post"
"Generated by AI"

Return ONLY the final Telegram post.

IMPORTANT:
The final post MUST contain between
20 and 50 words.
"""


# ============================================================
# GENERATE ADMIN POST
# ============================================================

async def generate_admin_post(
    chat_id
):

    await send_message(
        chat_id,
        f"""
⏳ *Ana Generate Post...*

📚 Topic:
{state["topic"]}

📏 Length:
20–50 words

🔎 Search:
{"ON" if state["search_enabled"] else "OFF"}
"""
    )

    try:

        config_args = {

            "max_output_tokens": 250

        }

        if state[
            "search_enabled"
        ]:

            config_args[
                "tools"
            ] = [

                types.Tool(
                    google_search=types.GoogleSearch()
                )

            ]

        response = await asyncio.to_thread(

            gemini.models.generate_content,

            model=GEMINI_MODEL,

            contents=admin_post_prompt(),

            config=types.GenerateContentConfig(
                **config_args
            )

        )

        post = getattr(
            response,
            "text",
            ""
        )

        post = limit_words(
            post,
            20,
            50
        )

        if word_count(post) < 20:

            # We don't artificially invent content.
            # Generate again with a stronger instruction.
            raise RuntimeError(
                "Gemini returned fewer than "
                "20 words."
            )

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "post"
        ] = post

        users[chat_id][
            "post_time"
        ] = time_now()

        await send_message(
            chat_id,
            (
                "👀 *POST PREVIEW*\n\n"
                f"{post}\n\n"
                f"📏 Words: {word_count(post)}"
            ),
            preview_keyboard()
        )

    except ClientError as error:

        logger.exception(
            "Admin post generation error"
        )

        if getattr(
            error,
            "code",
            None
        ) == 429:

            await send_user_error(
                chat_id,
                "Gemini quota/rate limit ya cika. Ka jira kaɗan sannan ka danna Generate Again."
            )

        elif getattr(
            error,
            "code",
            None
        ) in (401, 403):

            await send_user_error(
                chat_id,
                "Gemini API key ko permission ɗinsa yana da matsala."
            )

        else:

            await send_user_error(
                chat_id,
                "Gemini ya samu matsala wajen Generate Post."
            )

    except Exception as error:

        logger.exception(
            "Admin post generation failed"
        )

        state[
            "last_error"
        ] = str(error)

        await send_user_error(
            chat_id,
            "Ba a iya Generate Post yanzu ba. Bot ɗin yana ci gaba da aiki."
        )


# ============================================================
# TIME
# ============================================================

def time_now():

    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# POST TO CHANNEL
# ============================================================

async def post_to_channel(
    post
):

    post = limit_words(
        post,
        20,
        50
    )

    return await send_message(
        CHANNEL_USERNAME,
        post
    )


# ============================================================
# ADMIN STATUS
# ============================================================

def admin_status():

    auto = (
        "🟢 ON"
        if state["auto_enabled"]
        else
        "🔴 OFF"
    )

    search = (
        "🟢 ON"
        if state["search_enabled"]
        else
        "🔴 OFF"
    )

    interval = INTERVALS.get(
        state["interval_minutes"],
        f"{state['interval_minutes']} minutes"
    )

    return f"""
📊 *ADMIN STATUS*

🔐 Access: ADMIN

🤖 Automatic: {auto}

📚 Topic:
{state["topic"]}

⏱ Interval:
{interval}

🔎 Web Search:
{search}

📢 Channel:
{CHANNEL_USERNAME}

🕐 Last Post:
{state["last_post"] or "None"}

⏰ Next Post:
{state["next_post"] or "None"}

⚠️ Last Error:
{state["last_error"] or "None"}
"""


# ============================================================
# CALLBACK HANDLER
# ============================================================

async def handle_callback(
    callback
):

    callback_id = callback.get(
        "id"
    )

    data = callback.get(
        "data",
        ""
    )

    message = callback.get(
        "message",
        {}
    )

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    await answer_callback(
        callback_id
    )

    # ========================================================
    # HOME
    # ========================================================

    if data == "home":

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "waiting_question"
        ] = False

        users[chat_id][
            "waiting_password"
        ] = False

        await send_welcome(
            chat_id
        )

        return

    # ========================================================
    # USER ASSISTANT
    # ========================================================

    if data == "user_assistant":

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "waiting_question"
        ] = True

        await send_message(
            chat_id,
            """
🤖 *WOKERS NG Assistant*

Rubuta tambayarka.

📚 WOKERS NG
🎓 Ilimi
💻 Technology / Programming

Amsa za ta kasance gajera.
""",
            user_keyboard()
        )

        return

    # ========================================================
    # ADMIN LOGIN
    # ========================================================

    if data == "admin_login":

        await start_admin_login(
            chat_id
        )

        return

    # ========================================================
    # EVERYTHING BELOW = ADMIN ONLY
    # ========================================================

    if data.startswith(
        "admin_"
    ) or data.startswith(
        "admin:"
    ):

        if not is_admin(
            chat_id
        ):

            await send_message(
                chat_id,
                """
🔒 *Admin Access Required*

Danna Admin Assistant sannan
ka tabbatar da password.
""",
                home_keyboard()
            )

            return

    # ========================================================
    # ADMIN HOME
    # ========================================================

    if data == "admin_home":

        await send_admin_panel(
            chat_id
        )

        return

    # ========================================================
    # GENERATE
    # ========================================================

    if data == "admin_generate":

        await generate_admin_post(
            chat_id
        )

        return

    # ========================================================
    # GENERATE AGAIN
    # ========================================================

    if data == "admin_again":

        await generate_admin_post(
            chat_id
        )

        return

    # ========================================================
    # PREVIEW
    # ========================================================

    if data == "admin_preview":

        post = users.get(
            chat_id,
            {}
        ).get(
            "post"
        )

        if not post:

            await send_message(
                chat_id,
                """
👀 Babu post preview yanzu.

Danna Generate Post.
""",
                admin_keyboard()
            )

            return

        await send_message(
            chat_id,
            (
                "👀 *CURRENT PREVIEW*\n\n"
                f"{post}\n\n"
                f"📏 Words: {word_count(post)}"
            ),
            preview_keyboard()
        )

        return

    # ========================================================
    # POST NOW
    # ========================================================

    if data == "admin_post":

        post = users.get(
            chat_id,
            {}
        ).get(
            "post"
        )

        if not post:

            await send_message(
                chat_id,
                "❌ Babu post da za a aika.",
                admin_keyboard()
            )

            return

        try:

            await post_to_channel(
                post
            )

            state[
                "last_post"
            ] = time_now()

            state[
                "last_error"
            ] = None

            users[chat_id][
                "post"
            ] = None

            await send_message(
                chat_id,
                f"""
✅ *POSTED SUCCESSFULLY*

📢 {CHANNEL_USERNAME}

📏 Words:
{word_count(post)}
""",
                admin_keyboard()
            )

        except Exception as error:

            logger.exception(
                "Channel post error"
            )

            state[
                "last_error"
            ] = str(error)

            await send_user_error(
                chat_id,
                "Ba a iya aika post zuwa channel ba. Ka tabbatar bot Admin ne."
            )

        return

    # ========================================================
    # TOPICS
    # ========================================================

    if data == "admin_topics":

        await send_message(
            chat_id,
            """
📚 *Choose Post Topic*
""",
            topic_keyboard()
        )

        return

    # ========================================================
    # TOPIC SELECTED
    # ========================================================

    if data.startswith(
        "admin_topic:"
    ):

        topic = data.split(
            ":",
            1
        )[1]

        if topic not in TOPICS:

            await send_message(
                chat_id,
                "❌ Topic bai samu ba.",
                topic_keyboard()
            )

            return

        state[
            "topic"
        ] = topic

        state[
            "custom_topic"
        ] = ""

        await send_message(
            chat_id,
            f"""
✅ *Topic Updated*

📚 {topic}

Za a yi amfani da wannan topic
a Generate da Automatic Posting.
""",
            admin_keyboard()
        )

        return

    # ========================================================
    # CUSTOM TOPIC
    # ========================================================

    if data == "admin_custom_topic":

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "waiting_custom_topic"
        ] = True

        await send_message(
            chat_id,
            """
✍️ *Custom Topic*

Rubuta topic ɗin da kake so.

Misali:

"Sabon AI tool ga ɗalibai"
"""
        )

        return

    # ========================================================
    # AUTO MENU
    # ========================================================

    if data == "admin_auto":

        state[
            "auto_enabled"
        ] = not state[
            "auto_enabled"
        ]

        if state[
            "auto_enabled"
        ]:

            next_time = (
                datetime.now()
                + timedelta(
                    minutes=state[
                        "interval_minutes"
                    ]
                )
            )

            state[
                "next_post"
            ] = next_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        else:

            state[
                "next_post"
            ] = None

        await send_message(
            chat_id,
            (
                "🟢 Automatic Posting ON"
                if state["auto_enabled"]
                else
                "🔴 Automatic Posting OFF"
            ),
            admin_keyboard()
        )

        return

    # ========================================================
    # INTERVAL MENU
    # ========================================================

    if data == "admin_interval":

        await send_message(
            chat_id,
            """
⏱ *Automatic Posting Interval*

Zaɓi lokacin da bot zai jira
kafin ya yi sabon post.
""",
            interval_keyboard()
        )

        return

    # ========================================================
    # INTERVAL SELECTED
    # ========================================================

    if data.startswith(
        "admin_interval:"
    ):

        try:

            minutes = int(
                data.split(
                    ":",
                    1
                )[1]
            )

        except Exception:

            await send_message(
                chat_id,
                "❌ Invalid interval."
            )

            return

        if minutes not in INTERVALS:

            await send_message(
                chat_id,
                "❌ Interval bai samu ba."
            )

            return

        state[
            "interval_minutes"
        ] = minutes

        if state[
            "auto_enabled"
        ]:

            next_time = (
                datetime.now()
                + timedelta(
                    minutes=minutes
                )
            )

            state[
                "next_post"
            ] = next_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

        await send_message(
            chat_id,
            f"""
✅ *Interval Updated*

⏱ {INTERVALS[minutes]}
""",
            admin_keyboard()
        )

        return

    # ========================================================
    # SEARCH
    # ========================================================

    if data == "admin_search":

        state[
            "search_enabled"
        ] = not state[
            "search_enabled"
        ]

        await send_message(
            chat_id,
            (
                "🔎 Web Search: "
                + (
                    "🟢 ON"
                    if state["search_enabled"]
                    else
                    "🔴 OFF"
                )
            ),
            admin_keyboard()
        )

        return

    # ========================================================
    # STATUS
    # ========================================================

    if data == "admin_status":

        await send_message(
            chat_id,
            admin_status(),
            admin_keyboard()
        )

        return

    # ========================================================
    # LOGOUT
    # ========================================================

    if data == "admin_logout":

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "admin"
        ] = False

        users[chat_id][
            "waiting_password"
        ] = False

        await send_message(
            chat_id,
            """
🚪 *Admin Logged Out*

An rufe Admin Access.
""",
            home_keyboard()
        )

        return


# ============================================================
# MESSAGE HANDLER
# ============================================================

async def handle_message(
    message
):

    chat = message.get(
        "chat",
        {}
    )

    chat_id = chat.get(
        "id"
    )

    if not chat_id:

        return

    text = message.get(
        "text",
        ""
    ).strip()

    if not text:

        return

    # ========================================================
    # USER DATA
    # ========================================================

    users.setdefault(
        chat_id,
        {}
    )

    current = users[
        chat_id
    ]

    # ========================================================
    # ADMIN PASSWORD
    # ========================================================

    if current.get(
        "waiting_password"
    ):

        # Never log password.
        await verify_admin_password(
            chat_id,
            text
        )

        return

    # ========================================================
    # CUSTOM TOPIC
    # ========================================================

    if current.get(
        "waiting_custom_topic"
    ):

        if not is_admin(
            chat_id
        ):

            current[
                "waiting_custom_topic"
            ] = False

            await send_message(
                chat_id,
                "🔒 Admin Access Required.",
                home_keyboard()
            )

            return

        topic = text[:300]

        state[
            "topic"
        ] = "Custom"

        state[
            "custom_topic"
        ] = topic

        current[
            "waiting_custom_topic"
        ] = False

        await send_message(
            chat_id,
            f"""
✅ *Custom Topic Saved*

📚 {topic}
""",
            admin_keyboard()
        )

        return

    # ========================================================
    # USER QUESTION
    # ========================================================

    if current.get(
        "waiting_question"
    ):

        await answer_user_question(
            chat_id,
            text
        )

        return

    # ========================================================
    # START
    # ========================================================

    if text.lower() == "/start":

        current[
            "waiting_question"
        ] = False

        current[
            "waiting_password"
        ] = False

        await send_welcome(
            chat_id
        )

        return

    # ========================================================
    # ADMIN COMMANDS
    # ========================================================

    if text.lower() == "/admin":

        if is_admin(
            chat_id
        ):

            await send_admin_panel(
                chat_id
            )

        else:

            await start_admin_login(
                chat_id
            )

        return

    # ========================================================
    # GENERATE COMMAND
    # ========================================================

    if text.lower() == "/generate":

        if not is_admin(
            chat_id
        ):

            await send_message(
                chat_id,
                "🔒 Wannan command na Admin ne.",
                home_keyboard()
            )

            return

        await generate_admin_post(
            chat_id
        )

        return

    # ========================================================
    # STATUS
    # ========================================================

    if text.lower() == "/status":

        if not is_admin(
            chat_id
        ):

            await send_message(
                chat_id,
                "🔒 Wannan command na Admin ne.",
                home_keyboard()
            )

            return

        await send_message(
            chat_id,
            admin_status(),
            admin_keyboard()
        )

        return

    # ========================================================
    # HELP
    # ========================================================

    if text.lower() == "/help":

        await send_message(
            chat_id,
            """
📚 *WOKERS NG*

/start - Welcome
/ask - Tambayar Assistant
/admin - Admin Login

User:
🎓 Ilimi
🤖 WOKERS NG
💻 Digital skills

Admin:
🛠 Posts
⚙️ Automatic
📊 Settings
"""
        )

        return

    # ========================================================
    # /ASK
    # ========================================================

    if text.lower() == "/ask":

        current[
            "waiting_question"
        ] = True

        await send_message(
            chat_id,
            """
🤖 Rubuta tambayar ka.

Tambayar ta kasance akan
WOKERS NG ko ilimi.
"""
        )

        return

    # ========================================================
    # DEFAULT USER BEHAVIOUR
    # ========================================================

    # Do not expose admin features.
    # Treat ordinary messages as user questions.
    await answer_user_question(
        chat_id,
        text
    )


# ============================================================
# UPDATE PROCESSOR
# ============================================================

async def process_update(
    update
):

    try:

        if "callback_query" in update:

            await handle_callback(
                update[
                    "callback_query"
                ]
            )

            return

        if "message" in update:

            await handle_message(
                update[
                    "message"
                ]
            )

            return

    except Exception as error:

        # IMPORTANT:
        # Never kill webhook process.
        logger.exception(
            "UPDATE PROCESSING ERROR"
        )


# ============================================================
# TELEGRAM WEBHOOK
# ============================================================

@app.route(
    "/telegram-webhook",
    methods=["POST"]
)
def telegram_webhook():

    if WEBHOOK_SECRET:

        received = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if received != WEBHOOK_SECRET:

            return jsonify({
                "ok": False
            }), 403

    update = request.get_json(
        silent=True
    )

    if not update:

        return jsonify({
            "ok": True
        })

    def runner():

        try:

            asyncio.run(
                process_update(
                    update
                )
            )

        except Exception:

            logger.exception(
                "Webhook background error"
            )

    threading.Thread(
        target=runner,
        daemon=True
    ).start()

    return jsonify({
        "ok": True
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/")
def index():

    return jsonify({

        "status": "online",

        "service": "WOKERS NG AI Bot",

        "image_generation": False,

        "channel": CHANNEL_USERNAME,

        "automatic": state[
            "auto_enabled"
        ]

    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# ============================================================
# AUTOMATIC POST WORKER
# ============================================================

async def automatic_worker():

    logger.info(
        "Automatic worker started."
    )

    while True:

        try:

            if not state[
                "auto_enabled"
            ]:

                state[
                    "next_post"
                ] = None

                await asyncio.sleep(
                    10
                )

                continue

            minutes = state[
                "interval_minutes"
            ]

            next_time = (
                datetime.now()
                + timedelta(
                    minutes=minutes
                )
            )

            state[
                "next_post"
            ] = next_time.strftime(
                "%Y-%m-%d %H:%M:%S"
            )

            logger.info(
                "Next automatic post: %s",
                state["next_post"]
            )

            # ------------------------------------------------
            # Wait
            # ------------------------------------------------

            await asyncio.sleep(
                minutes * 60
            )

            # ------------------------------------------------
            # Check if still enabled
            # ------------------------------------------------

            if not state[
                "auto_enabled"
            ]:

                continue

            # ------------------------------------------------
            # Generate
            # ------------------------------------------------

            try:

                config_args = {

                    "max_output_tokens": 250

                }

                if state[
                    "search_enabled"
                ]:

                    config_args[
                        "tools"
                    ] = [

                        types.Tool(
                            google_search=types.GoogleSearch()
                        )

                    ]

                response = await asyncio.to_thread(

                    gemini.models.generate_content,

                    model=GEMINI_MODEL,

                    contents=admin_post_prompt(),

                    config=types.GenerateContentConfig(
                        **config_args
                    )

                )

                post = getattr(
                    response,
                    "text",
                    ""
                )

                post = limit_words(
                    post,
                    20,
                    50
                )

                if word_count(post) < 20:

                    raise RuntimeError(
                        "Automatic AI response "
                        "was below 20 words."
                    )

            except ClientError as error:

                logger.exception(
                    "AUTOMATIC GEMINI ERROR"
                )

                state[
                    "last_error"
                ] = str(error)

                # Do NOT stop worker.
                await asyncio.sleep(
                    30
                )

                continue

            except Exception as error:

                logger.exception(
                    "AUTOMATIC GENERATION ERROR"
                )

                state[
                    "last_error"
                ] = str(error)

                await asyncio.sleep(
                    30
                )

                continue

            # ------------------------------------------------
            # Post
            # ------------------------------------------------

            try:

                await post_to_channel(
                    post
                )

                state[
                    "last_post"
                ] = time_now()

                state[
                    "last_error"
                ] = None

                logger.info(
                    "Automatic post successfully published."
                )

            except Exception as error:

                logger.exception(
                    "AUTOMATIC TELEGRAM POST ERROR"
                )

                state[
                    "last_error"
                ] = str(error)

                # Do not kill worker.
                await asyncio.sleep(
                    30
                )

        except asyncio.CancelledError:

            logger.info(
                "Automatic worker cancelled."
            )

            return

        except Exception as error:

            # Final safety net.
            logger.exception(
                "AUTOMATIC WORKER UNEXPECTED ERROR"
            )

            state[
                "last_error"
            ] = str(error)

            await asyncio.sleep(
                30
            )


# ============================================================
# WEBHOOK SETUP
# ============================================================

async def setup_webhook():

    if not RENDER_EXTERNAL_URL:

        logger.warning(
            "RENDER_EXTERNAL_URL is missing."
        )

        return

    webhook_url = (
        RENDER_EXTERNAL_URL.rstrip("/")
        + "/telegram-webhook"
    )

    data = {

        "url": webhook_url,

        "allowed_updates": [
            "message",
            "callback_query"
        ]

    }

    if WEBHOOK_SECRET:

        data[
            "secret_token"
        ] = WEBHOOK_SECRET

    try:

        result = await telegram_request(
            "setWebhook",
            data
        )

        logger.info(
            "Webhook configured: %s",
            result.get("ok")
        )

    except Exception as error:

        logger.exception(
            "Webhook setup failed"
        )


# ============================================================
# WORKER THREAD
# ============================================================

def start_worker():

    def runner():

        while True:

            try:

                asyncio.run(
                    automatic_worker()
                )

            except Exception as error:

                # If somehow worker itself dies,
                # restart it instead of killing bot.
                logger.exception(
                    "WORKER STOPPED; RESTARTING: %s",
                    error
                )

                time_to_wait = 10

                import time

                time.sleep(
                    time_to_wait
                )

    thread = threading.Thread(
        target=runner,
        daemon=True,
        name="wokers-ng-auto-worker"
    )

    thread.start()


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "========================================"
    )

    logger.info(
        "WOKERS NG AI BOT STARTING"
    )

    logger.info(
        "Model: %s",
        GEMINI_MODEL
    )

    logger.info(
        "Channel: %s",
        CHANNEL_USERNAME
    )

    logger.info(
        "Image generation: DISABLED"
    )

    logger.info(
        "User answer limit: 5-20 words"
    )

    logger.info(
        "Post limit: 20-50 words"
    )

    logger.info(
        "========================================"
    )

    # --------------------------------------------------------
    # Webhook
    # --------------------------------------------------------

    try:

        asyncio.run(
            setup_webhook()
        )

    except Exception:

        logger.exception(
            "Webhook startup exception"
        )

    # --------------------------------------------------------
    # Automatic worker
    # --------------------------------------------------------

    start_worker()

    # --------------------------------------------------------
    # Render PORT
    # --------------------------------------------------------

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# ============================================================
# ENTRY POINT
# ============================================================

if __name__ == "__main__":
    main()