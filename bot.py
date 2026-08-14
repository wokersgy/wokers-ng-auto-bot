import os
import re
import json
import time
import asyncio
import logging
import threading
from datetime import datetime, timedelta
from collections import deque

import httpx
from flask import Flask, request, jsonify

from google import genai
from google.genai import types
from google.genai.errors import ClientError


# ============================================================
# WOKERS NG AI TELEGRAM BOT
# ============================================================

APP_NAME = "WOKERS NG AI Bot"


# ============================================================
# LOGGING
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger(APP_NAME)


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

PORT = int(
    os.getenv(
        "PORT",
        "10000"
    )
)


# ============================================================
# STARTUP VALIDATION
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
# CLIENTS
# ============================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)

app = Flask(__name__)

TELEGRAM_API = (
    "https://api.telegram.org/"
    f"bot{BOT_TOKEN}"
)


# ============================================================
# RUNTIME STATE
# ============================================================

state = {

    "auto_enabled": False,

    "interval_minutes": 120,

    "topic": "Technology",

    "custom_topic": "",

    "search_enabled": True,

    "last_post": None,

    "next_post": None,

    "last_error": None,

    "last_generation": None

}


# ============================================================
# USER SESSIONS
# ============================================================

users = {}


# ============================================================
# ERROR STORAGE
# ============================================================

error_log = deque(
    maxlen=50
)


def record_error(
    source,
    error,
    extra=""
):

    try:

        error_text = str(
            error
        )

        # Never save bot token/API key
        error_text = error_text.replace(
            BOT_TOKEN,
            "[BOT_TOKEN_HIDDEN]"
        )

        error_text = error_text.replace(
            GEMINI_API_KEY,
            "[GEMINI_KEY_HIDDEN]"
        )

        entry = {

            "time":
                datetime.now().strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),

            "source":
                str(source),

            "error":
                error_text[:2000],

            "extra":
                str(extra)[:500]

        }

        error_log.append(
            entry
        )

        state[
            "last_error"
        ] = error_text[:1000]

        logger.error(
            "%s | %s | %s",
            source,
            error_text[:1000],
            extra
        )

    except Exception:

        logger.exception(
            "Could not record error."
        )


# ============================================================
# WORD HELPERS
# ============================================================

def words(text):

    return re.findall(
        r"\S+",
        str(text or "")
    )


def word_count(text):

    return len(
        words(text)
    )


def limit_words(
    text,
    maximum
):

    text = clean_text(
        text
    )

    w = words(
        text
    )

    if len(w) <= maximum:
        return text

    return (
        " ".join(
            w[:maximum]
        )
        .rstrip(
            ".,!?;:"
        )
        + "..."
    )


def clean_text(
    text
):

    if not text:
        return ""

    text = str(
        text
    ).strip()

    text = re.sub(
        r"^```(?:text|markdown)?",
        "",
        text,
        flags=re.I
    )

    text = re.sub(
        r"```$",
        "",
        text
    )

    text = text.strip()

    prefixes = [

        "Here is the post:",

        "Here is your post:",

        "Here is the answer:",

        "Ga post:",

        "Ga amsar:",

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
# TELEGRAM API
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
        ) as client:

            response = await client.post(
                url,
                data=data or {}
            )

        result = response.json()

        if not result.get(
            "ok"
        ):

            description = result.get(
                "description",
                "Unknown Telegram error"
            )

            raise RuntimeError(
                f"Telegram API {method}: "
                f"{description}"
            )

        return result

    except Exception as error:

        record_error(
            "telegram_request",
            error,
            method
        )

        raise


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_message(
    chat_id,
    text,
    keyboard=None
):

    text = str(
        text or ""
    ).strip()

    # Telegram limit
    if len(text) > 4096:

        text = text[:4090] + "..."

    data = {

        "chat_id":
            chat_id,

        "text":
            text,

        "disable_web_page_preview":
            "false"

    }

    if keyboard:

        data[
            "reply_markup"
        ] = json.dumps(
            keyboard,
            ensure_ascii=False
        )

    return await telegram_request(
        "sendMessage",
        data
    )


# ============================================================
# SAFE ERROR MESSAGE
# ============================================================

async def send_safe_error(
    chat_id,
    message
):

    try:

        await send_message(
            chat_id,
            (
                "⚠️ *Matsala ta faru*\n\n"
                f"{message}\n\n"
                "Bot ɗin yana ci gaba da aiki."
            )
        )

    except Exception as error:

        record_error(
            "send_safe_error",
            error
        )


# ============================================================
# CALLBACK
# ============================================================

async def answer_callback(
    callback_id,
    text=""
):

    data = {

        "callback_query_id":
            callback_id

    }

    if text:

        data[
            "text"
        ] = text

    try:

        await telegram_request(
            "answerCallbackQuery",
            data
        )

    except Exception as error:

        record_error(
            "answer_callback",
            error
        )


# ============================================================
# USER KEYBOARD
# ============================================================

def user_keyboard():

    return {

        "inline_keyboard": [

            [

                {
                    "text":
                        "🤖 Ask Assistant",

                    "callback_data":
                        "user_assistant"
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
                    "text":
                        "🚀 Generate Post",

                    "callback_data":
                        "generate"
                },

                {
                    "text":
                        "👀 Preview",

                    "callback_data":
                        "preview"
                }

            ],

            [

                {
                    "text":
                        "🔄 Generate Again",

                    "callback_data":
                        "again"
                },

                {
                    "text":
                        "📢 Post Now",

                    "callback_data":
                        "post"
                }

            ],

            [

                {
                    "text":
                        (
                            "🟢 Auto ON"
                            if auto
                            else
                            "🔴 Auto OFF"
                        ),

                    "callback_data":
                        "auto"
                },

                {
                    "text":
                        "⏱ Interval",

                    "callback_data":
                        "interval"
                }

            ],

            [

                {
                    "text":
                        "📚 Topic",

                    "callback_data":
                        "topics"
                },

                {
                    "text":
                        (
                            "🔎 Search ON"
                            if state[
                                "search_enabled"
                            ]
                            else
                            "🔎 Search OFF"
                        ),

                    "callback_data":
                        "search"
                }

            ],

            [

                {
                    "text":
                        "📊 Status",

                    "callback_data":
                        "status"
                },

                {
                    "text":
                        "🐞 Errors",

                    "callback_data":
                        "errors"
                }

            ],

            [

                {
                    "text":
                        "🧹 Clear Errors",

                    "callback_data":
                        "clear_errors"
                }

            ],

            [

                {
                    "text":
                        "🚪 Logout",

                    "callback_data":
                        "logout"
                }

            ]

        ]

    }


# ============================================================
# TOPICS
# ============================================================

TOPICS = {

    "AI":
        "Artificial Intelligence and useful AI tools",

    "Technology":
        "Technology and digital trends",

    "Apps":
        "Mobile applications and useful apps",

    "Websites":
        "Useful websites and online services",

    "Programming":
        "Programming and software development",

    "Jobs":
        "Digital skills and career education",

    "Freelancing":
        "Freelancing and digital work",

    "Business":
        "Digital business and entrepreneurship",

    "Education":
        "Education and learning",

    "Cybersecurity":
        "Cybersecurity and online safety",

    "Phones":
        "Android, smartphones and mobile technology",

    "Internet":
        "Internet and useful digital services",

    "Productivity":
        "Productivity and digital skills",

    "News":
        "Technology news and recent developments"

}


def topic_keyboard():

    rows = []

    names = list(
        TOPICS.keys()
    )

    for i in range(
        0,
        len(names),
        2
    ):

        row = []

        for topic in names[
            i:i + 2
        ]:

            row.append({

                "text":
                    topic,

                "callback_data":
                    "topic:" + topic

            })

        rows.append(
            row
        )

    rows.append([

        {
            "text":
                "✍️ Custom Topic",

            "callback_data":
                "custom_topic"
        }

    ])

    rows.append([

        {
            "text":
                "⬅️ Admin",

            "callback_data":
                "admin_home"
        }

    ])

    return {
        "inline_keyboard":
            rows
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


def interval_keyboard():

    rows = []

    items = list(
        INTERVALS.items()
    )

    for i in range(
        0,
        len(items),
        2
    ):

        row = []

        for minutes, label in items[
            i:i + 2
        ]:

            row.append({

                "text":
                    label,

                "callback_data":
                    f"interval:{minutes}"

            })

        rows.append(
            row
        )

    rows.append([

        {
            "text":
                "⬅️ Admin",

            "callback_data":
                "admin_home"
        }

    ])

    return {
        "inline_keyboard":
            rows
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
# WELCOME
# ============================================================

async def welcome(
    chat_id
):

    await send_message(
        chat_id,
        """
👋 *Barka da zuwa WOKERS NG*

🤖 WOKERS NG Assistant yana taimaka maka da:

📚 Ilimi
💻 Technology
🤖 AI
👨‍💻 Programming
📱 Apps
🌐 Websites
💼 Digital skills

Za ka iya tambaya a kowane lokaci.
""",
        user_keyboard()
    )


# ============================================================
# ADMIN LOGIN
# ============================================================

async def admin_login(
    chat_id
):

    users.setdefault(
        chat_id,
        {}
    )

    users[chat_id][
        "waiting_password"
    ] = True

    await send_message(
        chat_id,
        """
🔐 *Admin Login*

Rubuta Admin Password:
"""
    )


# ============================================================
# ADMIN PANEL
# ============================================================

async def admin_panel(
    chat_id
):

    auto = (
        "🟢 ON"
        if state[
            "auto_enabled"
        ]
        else
        "🔴 OFF"
    )

    search = (
        "🟢 ON"
        if state[
            "search_enabled"
        ]
        else
        "🔴 OFF"
    )

    await send_message(
        chat_id,
        f"""
🛠 *WOKERS NG ADMIN*

🔐 Access: GRANTED

📚 Topic:
{state["topic"]}

🤖 Automatic:
{auto}

⏱ Interval:
{INTERVALS.get(
    state["interval_minutes"],
    "Custom"
)}

🔎 Search:
{search}

📢 Channel:
{CHANNEL_USERNAME}

🐞 Errors:
{len(error_log)}
""",
        admin_keyboard()
    )


# ============================================================
# GEMINI ERROR TYPE
# ============================================================

def gemini_error_code(
    error
):

    return getattr(
        error,
        "code",
        None
    )


# ============================================================
# GEMINI GENERATION
# ============================================================

async def gemini_generate(
    prompt,
    use_search=False,
    max_tokens=250
):

    config_kwargs = {

        "max_output_tokens":
            max_tokens

    }

    # Google Search grounding
    if use_search:

        config_kwargs[
            "tools"
        ] = [

            types.Tool(
                google_search=
                    types.GoogleSearch()
            )

        ]

    try:

        response = await asyncio.to_thread(

            gemini.models.generate_content,

            model=GEMINI_MODEL,

            contents=prompt,

            config=
                types.GenerateContentConfig(
                    **config_kwargs
                )

        )

        text = getattr(
            response,
            "text",
            ""
        )

        if not text:

            raise RuntimeError(
                "Gemini returned empty response."
            )

        return text

    except ClientError as error:

        code = gemini_error_code(
            error
        )

        record_error(
            "gemini_generate",
            error,
            f"code={code}, search={use_search}"
        )

        raise

    except Exception as error:

        record_error(
            "gemini_generate",
            error,
            f"search={use_search}"
        )

        raise


# ============================================================
# USER PROMPT
# ============================================================

def user_prompt(
    question
):

    return f"""
You are WOKERS NG Assistant.

User question:
{question}

Answer ONLY if it is related to:
WOKERS NG, education, learning, technology,
programming, AI education, apps, websites,
digital skills or career learning.

If unrelated, politely say:
"Zan iya taimakawa da WOKERS NG ko ilimi kawai."

Rules:
- Natural Nigerian Hausa.
- Maximum 20 words.
- Keep answer direct.
- No long explanation.
- No "As an AI".
- Do not invent facts.
- Return only the answer.
"""


# ============================================================
# USER ASSISTANT
# ============================================================

async def user_assistant(
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

    try:

        answer = await gemini_generate(
            user_prompt(
                question[:1500]
            ),
            use_search=False,
            max_tokens=100
        )

        answer = limit_words(
            answer,
            20
        )

        await send_message(
            chat_id,
            answer,
            user_keyboard()
        )

    except ClientError as error:

        if gemini_error_code(
            error
        ) == 429:

            await send_safe_error(
                chat_id,
                "Gemini quota ya cika. Ka sake gwadawa daga baya."
            )

        elif gemini_error_code(
            error
        ) in (401, 403):

            await send_safe_error(
                chat_id,
                "Gemini API key ko permission yana da matsala."
            )

        else:

            await send_safe_error(
                chat_id,
                "Gemini ya kasa amsa yanzu."
            )

    except Exception as error:

        record_error(
            "user_assistant",
            error
        )

        await send_safe_error(
            chat_id,
            "An samu matsala wajen Assistant."
        )


# ============================================================
# POST PROMPT
# ============================================================

def post_prompt():

    if state[
        "topic"
    ] == "Custom":

        topic = state[
            "custom_topic"
        ]

    else:

        topic = TOPICS.get(
            state[
                "topic"
            ],
            state[
                "topic"
            ]
        )

    return f"""
Create one short Telegram post for WOKERS NG.

TOPIC:
{topic}

LANGUAGE:
Natural Nigerian Hausa.

IMPORTANT:
- 20 to 50 words ONLY.
- Useful and informative.
- Professional.
- Human-like.
- Short sentences.
- A few suitable emojis are allowed.
- No long introduction.
- No unnecessary explanation.
- Do not say "As an AI".
- Do not say "Here is the post".
- Do not invent facts.
- Do not invent URLs.

If you use a URL, it must be a real URL
returned by web search.

Return ONLY the final post.
"""


# ============================================================
# GENERATE POST
# ============================================================

async def generate_post(
    chat_id
):

    await send_message(
        chat_id,
        """
⏳ *Generating Post...*

Ana ƙirƙirar short Hausa post
20–50 words.
"""
    )

    try:

        # First try with search if enabled.
        try:

            text = await gemini_generate(
                post_prompt(),
                use_search=
                    state[
                        "search_enabled"
                    ],
                max_tokens=250
            )

        except ClientError as search_error:

            # If search/tool is the problem,
            # retry WITHOUT search.
            if state[
                "search_enabled"
            ]:

                record_error(
                    "gemini_search_fallback",
                    search_error
                )

                text = await gemini_generate(
                    post_prompt(),
                    use_search=False,
                    max_tokens=250
                )

            else:

                raise

        post = clean_text(
            text
        )

        post = limit_words(
            post,
            50
        )

        if word_count(post) < 20:

            # One retry without search
            # and stronger instruction.
            retry_prompt = (
                post_prompt()
                + """

IMPORTANT:
Your previous answer was too short.
Create a NEW post with at least 20 words
and no more than 50 words.
"""
            )

            retry_text = await gemini_generate(
                retry_prompt,
                use_search=False,
                max_tokens=250
            )

            post = limit_words(
                retry_text,
                50
            )

        if not post:

            raise RuntimeError(
                "Empty generated post."
            )

        users.setdefault(
            chat_id,
            {}
        )

        users[chat_id][
            "post"
        ] = post

        state[
            "last_generation"
        ] = time_now()

        await send_message(
            chat_id,
            (
                "👀 *POST PREVIEW*\n\n"
                f"{post}\n\n"
                f"📏 Words: {word_count(post)}"
            ),
            {
                "inline_keyboard": [

                    [
                        {
                            "text":
                                "📢 Post Now",

                            "callback_data":
                                "post"
                        },

                        {
                            "text":
                                "🔄 Generate Again",

                            "callback_data":
                                "again"
                        }

                    ],

                    [
                        {
                            "text":
                                "⬅️ Admin",

                            "callback_data":
                                "admin_home"
                        }
                    ]

                ]
            }
        )

    except ClientError as error:

        code = gemini_error_code(
            error
        )

        if code == 429:

            await send_safe_error(
                chat_id,
                (
                    "Gemini quota/rate limit ya cika. "
                    "Ba matsalar Telegram ba. "
                    "Ka jira quota ya dawo sannan ka danna Generate Again."
                )
            )

        elif code in (401, 403):

            await send_safe_error(
                chat_id,
                "Gemini API key ba shi da ingantaccen access."
            )

        else:

            await send_safe_error(
                chat_id,
                f"Gemini error HTTP {code or 'unknown'}."
            )

    except Exception as error:

        record_error(
            "generate_post",
            error
        )

        await send_safe_error(
            chat_id,
            "Ba a iya Generate Post yanzu ba."
        )


# ============================================================
# POST CHANNEL
# ============================================================

async def post_channel(
    post
):

    post = clean_text(
        post
    )

    # Ensure Telegram safety
    if len(post) > 4096:

        post = post[:4090] + "..."

    return await telegram_request(
        "sendMessage",
        {

            "chat_id":
                CHANNEL_USERNAME,

            "text":
                post,

            "disable_web_page_preview":
                "false"

        }
    )


# ============================================================
# ERROR REPORT
# ============================================================

def format_errors():

    if not error_log:

        return """
🐞 *ERROR LOG*

✅ Babu errors da aka ajiye.
"""

    lines = [
        "🐞 *WOKERS NG ERROR LOG*",
        ""
    ]

    entries = list(
        error_log
    )[-10:]

    for index, entry in enumerate(
        entries,
        1
    ):

        lines.append(
            f"#{index} "
            f"{entry['time']}"
        )

        lines.append(
            f"Source: {entry['source']}"
        )

        lines.append(
            f"Error: {entry['error'][:500]}"
        )

        if entry[
            "extra"
        ]:

            lines.append(
                f"Info: {entry['extra']}"
            )

        lines.append(
            "----------------"
        )

    text = "\n".join(
        lines
    )

    # Telegram max safety
    if len(text) > 3900:

        text = text[-3900:]

    return text


# ============================================================
# STATUS
# ============================================================

def status_text():

    return f"""
📊 *WOKERS NG STATUS*

🤖 Automatic:
{"🟢 ON" if state["auto_enabled"] else "🔴 OFF"}

📚 Topic:
{state["topic"]}

⏱ Interval:
{INTERVALS.get(
    state["interval_minutes"],
    "Custom"
)}

🔎 Search:
{"🟢 ON" if state["search_enabled"] else "🔴 OFF"}

📢 Channel:
{CHANNEL_USERNAME}

🕐 Last Post:
{state["last_post"] or "None"}

⏰ Next Post:
{state["next_post"] or "None"}

🤖 Last Generation:
{state["last_generation"] or "None"}

🐞 Errors:
{len(error_log)}

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

    # --------------------------------------------------------
    # USER
    # --------------------------------------------------------

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

📚 Ilimi
💻 Technology
🤖 AI
👨‍💻 Programming
📱 Apps
🌐 Websites
""",
            user_keyboard()
        )

        return

    # --------------------------------------------------------
    # ADMIN SECURITY
    # --------------------------------------------------------

    # There is intentionally NO admin button
    # for normal users.

    if not is_admin(
        chat_id
    ):

        await send_message(
            chat_id,
            "🔒 Wannan aikin na Admin ne."
        )

        return

    # --------------------------------------------------------
    # ADMIN HOME
    # --------------------------------------------------------

    if data == "admin_home":

        await admin_panel(
            chat_id
        )

        return

    # --------------------------------------------------------
    # GENERATE
    # --------------------------------------------------------

    if data == "generate":

        await generate_post(
            chat_id
        )

        return

    # --------------------------------------------------------
    # AGAIN
    # --------------------------------------------------------

    if data == "again":

        await generate_post(
            chat_id
        )

        return

    # --------------------------------------------------------
    # PREVIEW
    # --------------------------------------------------------

    if data == "preview":

        post = users.get(
            chat_id,
            {}
        ).get(
            "post"
        )

        if not post:

            await send_message(
                chat_id,
                "👀 Babu Preview. Danna Generate Post.",
                admin_keyboard()
            )

            return

        await send_message(
            chat_id,
            (
                "👀 *PREVIEW*\n\n"
                f"{post}\n\n"
                f"📏 Words: {word_count(post)}"
            ),
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # POST
    # --------------------------------------------------------

    if data == "post":

        post = users.get(
            chat_id,
            {}
        ).get(
            "post"
        )

        if not post:

            await send_message(
                chat_id,
                "❌ Babu generated post.",
                admin_keyboard()
            )

            return

        try:

            await post_channel(
                post
            )

            state[
                "last_post"
            ] = time_now()

            state[
                "last_error"
            ] = None

            users[
                chat_id
            ][
                "post"
            ] = None

            await send_message(
                chat_id,
                """
✅ *POST SUCCESSFULLY PUBLISHED*
""",
                admin_keyboard()
            )

        except Exception as error:

            record_error(
                "manual_channel_post",
                error
            )

            await send_safe_error(
                chat_id,
                (
                    "Ba a iya post zuwa channel ba. "
                    "Ka tabbatar bot yana da permission na Post Messages."
                )
            )

        return

    # --------------------------------------------------------
    # AUTO
    # --------------------------------------------------------

    if data == "auto":

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
                    minutes=
                        state[
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

        await admin_panel(
            chat_id
        )

        return

    # --------------------------------------------------------
    # INTERVAL
    # --------------------------------------------------------

    if data == "interval":

        await send_message(
            chat_id,
            "⏱ Zaɓi Automatic Posting Interval:",
            interval_keyboard()
        )

        return

    if data.startswith(
        "interval:"
    ):

        try:

            minutes = int(
                data.split(
                    ":",
                    1
                )[1]
            )

            if minutes not in INTERVALS:

                raise ValueError(
                    "Invalid interval"
                )

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

            await admin_panel(
                chat_id
            )

        except Exception as error:

            record_error(
                "interval",
                error
            )

        return

    # --------------------------------------------------------
    # TOPICS
    # --------------------------------------------------------

    if data == "topics":

        await send_message(
            chat_id,
            "📚 Zaɓi Topic:",
            topic_keyboard()
        )

        return

    if data.startswith(
        "topic:"
    ):

        topic = data.split(
            ":",
            1
        )[1]

        if topic in TOPICS:

            state[
                "topic"
            ] = topic

            state[
                "custom_topic"
            ] = ""

        await admin_panel(
            chat_id
        )

        return

    # --------------------------------------------------------
    # CUSTOM TOPIC
    # --------------------------------------------------------

    if data == "custom_topic":

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
✍️ Rubuta Custom Topic ɗinka.

Misali:
"Sabbin AI tools ga students"
"""
        )

        return

    # --------------------------------------------------------
    # SEARCH
    # --------------------------------------------------------

    if data == "search":

        state[
            "search_enabled"
        ] = not state[
            "search_enabled"
        ]

        await admin_panel(
            chat_id
        )

        return

    # --------------------------------------------------------
    # STATUS
    # --------------------------------------------------------

    if data == "status":

        await send_message(
            chat_id,
            status_text(),
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # ERRORS
    # --------------------------------------------------------

    if data == "errors":

        await send_message(
            chat_id,
            format_errors(),
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # CLEAR ERRORS
    # --------------------------------------------------------

    if data == "clear_errors":

        error_log.clear()

        state[
            "last_error"
        ] = None

        await send_message(
            chat_id,
            "🧹 Error log an share.",
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # LOGOUT
    # --------------------------------------------------------

    if data == "logout":

        users.setdefault(
            chat_id,
            {}
        )

        users[
            chat_id
        ][
            "admin"
        ] = False

        users[
            chat_id
        ][
            "waiting_password"
        ] = False

        await welcome(
            chat_id
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

    users.setdefault(
        chat_id,
        {}
    )

    session = users[
        chat_id
    ]

    # --------------------------------------------------------
    # ADMIN PASSWORD
    # --------------------------------------------------------

    if session.get(
        "waiting_password"
    ):

        if text == ADMIN_PASSWORD:

            session[
                "admin"
            ] = True

            session[
                "waiting_password"
            ] = False

            await send_message(
                chat_id,
                "✅ *Admin Access Granted*"
            )

            await admin_panel(
                chat_id
            )

        else:

            await send_message(
                chat_id,
                "❌ Admin Password ba daidai ba."
            )

        return

    # --------------------------------------------------------
    # CUSTOM TOPIC
    # --------------------------------------------------------

    if session.get(
        "waiting_custom_topic"
    ):

        if not is_admin(
            chat_id
        ):

            session[
                "waiting_custom_topic"
            ] = False

            return

        state[
            "topic"
        ] = "Custom"

        state[
            "custom_topic"
        ] = text[:300]

        session[
            "waiting_custom_topic"
        ] = False

        await admin_panel(
            chat_id
        )

        return

    # --------------------------------------------------------
    # USER QUESTION MODE
    # --------------------------------------------------------

    if session.get(
        "waiting_question"
    ):

        await user_assistant(
            chat_id,
            text
        )

        return

    # --------------------------------------------------------
    # /START
    # --------------------------------------------------------

    if text.lower() == "/start":

        session[
            "admin"
        ] = False

        session[
            "waiting_password"
        ] = False

        session[
            "waiting_question"
        ] = False

        await welcome(
            chat_id
        )

        return

    # --------------------------------------------------------
    # /ADMIN
    # --------------------------------------------------------

    if text.lower() == "/admin":

        if is_admin(
            chat_id
        ):

            await admin_panel(
                chat_id
            )

        else:

            await admin_login(
                chat_id
            )

        return

    # --------------------------------------------------------
    # /ERRORS
    # --------------------------------------------------------

    if text.lower() == "/errors":

        if not is_admin(
            chat_id
        ):

            await send_message(
                chat_id,
                "❌ Unknown command."
            )

            return

        await send_message(
            chat_id,
            format_errors(),
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # /CLEARERRORS
    # --------------------------------------------------------

    if text.lower() == "/clearerrors":

        if not is_admin(
            chat_id
        ):

            return

        error_log.clear()

        state[
            "last_error"
        ] = None

        await send_message(
            chat_id,
            "🧹 Error log an share.",
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # /GENERATE
    # --------------------------------------------------------

    if text.lower() == "/generate":

        if not is_admin(
            chat_id
        ):

            await send_message(
                chat_id,
                "❌ Unknown command."
            )

            return

        await generate_post(
            chat_id
        )

        return

    # --------------------------------------------------------
    # /STATUS
    # --------------------------------------------------------

    if text.lower() == "/status":

        if not is_admin(
            chat_id
        ):

            return

        await send_message(
            chat_id,
            status_text(),
            admin_keyboard()
        )

        return

    # --------------------------------------------------------
    # /ASK
    # --------------------------------------------------------

    if text.lower() == "/ask":

        session[
            "waiting_question"
        ] = True

        await send_message(
            chat_id,
            "🤖 Rubuta tambayarka.",
            user_keyboard()
        )

        return

    # --------------------------------------------------------
    # UNKNOWN COMMAND
    # --------------------------------------------------------

    if text.startswith(
        "/"
    ):

        await send_message(
            chat_id,
            "❌ Unknown command."
        )

        return

    # --------------------------------------------------------
    # NORMAL USER MESSAGE
    # --------------------------------------------------------

    await user_assistant(
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

        elif "message" in update:

            await handle_message(
                update[
                    "message"
                ]
            )

    except Exception as error:

        record_error(
            "process_update",
            error
        )

        logger.exception(
            "Update processing error"
        )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/telegram-webhook",
    methods=["POST"]
)
def webhook():

    try:

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

        if update:

            threading.Thread(
                target=lambda:
                    asyncio.run(
                        process_update(
                            update
                        )
                    ),
                daemon=True
            ).start()

        return jsonify({
            "ok": True
        })

    except Exception as error:

        record_error(
            "webhook",
            error
        )

        # Always return 200 to avoid
        # Telegram repeatedly retrying
        # malformed internal updates.
        return jsonify({
            "ok": True
        })


# ============================================================
# HEALTH
# ============================================================

@app.route("/")
def home():

    return jsonify({

        "status":
            "online",

        "bot":
            APP_NAME,

        "image_generation":
            False,

        "automatic":
            state[
                "auto_enabled"
            ],

        "channel":
            CHANNEL_USERNAME,

        "errors":
            len(error_log)

    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# ============================================================
# WEBHOOK SETUP
# ============================================================

async def setup_webhook():

    if not RENDER_EXTERNAL_URL:

        record_error(
            "webhook_setup",
            "RENDER_EXTERNAL_URL is missing."
        )

        return

    webhook_url = (
        RENDER_EXTERNAL_URL.rstrip("/")
        + "/telegram-webhook"
    )

    data = {

        "url":
            webhook_url,

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

        record_error(
            "setup_webhook",
            error
        )


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
                state[
                    "next_post"
                ]
            )

            await asyncio.sleep(
                minutes * 60
            )

            if not state[
                "auto_enabled"
            ]:

                continue

            # ------------------------------------------------
            # Generate automatic post
            # ------------------------------------------------

            try:

                try:

                    text = await gemini_generate(
                        post_prompt(),
                        use_search=
                            state[
                                "search_enabled"
                            ],
                        max_tokens=250
                    )

                except ClientError as search_error:

                    # Fallback without search
                    if state[
                        "search_enabled"
                    ]:

                        record_error(
                            "automatic_search_fallback",
                            search_error
                        )

                        text = await gemini_generate(
                            post_prompt(),
                            use_search=False,
                            max_tokens=250
                        )

                    else:

                        raise

                post = limit_words(
                    text,
                    50
                )

                if word_count(
                    post
                ) < 20:

                    retry_text = await gemini_generate(
                        post_prompt()
                        + """

Create a different post.
It MUST be 20–50 words.
""",
                        use_search=False,
                        max_tokens=250
                    )

                    post = limit_words(
                        retry_text,
                        50
                    )

            except ClientError as error:

                record_error(
                    "automatic_generation",
                    error
                )

                # Do not stop bot.
                await asyncio.sleep(
                    60
                )

                continue

            except Exception as error:

                record_error(
                    "automatic_generation",
                    error
                )

                await asyncio.sleep(
                    60
                )

                continue

            # ------------------------------------------------
            # Post to Telegram
            # ------------------------------------------------

            try:

                await post_channel(
                    post
                )

                state[
                    "last_post"
                ] = time_now()

                state[
                    "last_error"
                ] = None

                logger.info(
                    "Automatic post published."
                )

            except Exception as error:

                record_error(
                    "automatic_channel_post",
                    error
                )

                await asyncio.sleep(
                    60
                )

        except asyncio.CancelledError:

            logger.info(
                "Automatic worker stopped."
            )

            return

        except Exception as error:

            record_error(
                "automatic_worker",
                error
            )

            # IMPORTANT:
            # Never allow worker to die.
            await asyncio.sleep(
                30
            )


# ============================================================
# START AUTOMATIC WORKER
# ============================================================

def start_worker():

    def runner():

        while True:

            try:

                asyncio.run(
                    automatic_worker()
                )

            except Exception as error:

                record_error(
                    "worker_thread",
                    error
                )

                time.sleep(
                    15
                )

    thread = threading.Thread(
        target=runner,
        daemon=True,
        name="automatic-post-worker"
    )

    thread.start()


# ============================================================
# TIME
# ============================================================

def time_now():

    return datetime.now().strftime(
        "%Y-%m-%d %H:%M:%S"
    )


# ============================================================
# MAIN
# ============================================================

def main():

    logger.info(
        "===================================="
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
        "Admin: /admin only"
    )

    logger.info(
        "===================================="
    )

    try:

        asyncio.run(
            setup_webhook()
        )

    except Exception as error:

        record_error(
            "main_webhook",
            error
        )

    start_worker()

    app.run(
        host="0.0.0.0",
        port=PORT,
        debug=False,
        use_reloader=False
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()