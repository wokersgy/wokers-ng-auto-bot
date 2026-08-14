import os
import io
import json
import asyncio
import logging
import threading

import httpx
from flask import Flask, request, jsonify

from google import genai
from google.genai import types

from google.genai.errors import ClientError


# ============================================================
# WOKERS NG
# MANUAL GEMINI TELEGRAM POST BOT
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("wokers-ng")


# ============================================================
# ENVIRONMENT VARIABLES
# ============================================================

TELEGRAM_BOT_TOKEN = os.getenv(
    "TELEGRAM_BOT_TOKEN",
    ""
).strip()

GEMINI_API_KEY = os.getenv(
    "GEMINI_API_KEY",
    ""
).strip()

TELEGRAM_CHANNEL = os.getenv(
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


# ============================================================
# VALIDATION
# ============================================================

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is missing."
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing."
    )


# ============================================================
# GEMINI CLIENT
# ============================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# ============================================================
# FLASK
# ============================================================

app = Flask(__name__)


# ============================================================
# USER POST STORAGE
#
# Temporary memory only.
# If Render restarts, pending previews disappear.
# ============================================================

user_posts = {}


# ============================================================
# COLOR THEMES
# ============================================================

THEMES = {
    "emerald": {
        "name": "💚 Emerald",
        "colors": "emerald green, white and soft gold"
    },

    "blue": {
        "name": "💙 Ocean Blue",
        "colors": "deep blue, cyan and white"
    },

    "purple": {
        "name": "💜 Royal Purple",
        "colors": "purple, violet and white"
    },

    "orange": {
        "name": "🧡 Sunset Orange",
        "colors": "orange, coral and dark purple"
    },

    "gold": {
        "name": "💛 Premium Gold",
        "colors": "black, gold and white"
    },

    "teal": {
        "name": "🩵 Modern Teal",
        "colors": "teal, navy and white"
    },

    "red": {
        "name": "❤️ Bold Red",
        "colors": "red, black and white"
    },

    "lime": {
        "name": "💚 Fresh Lime",
        "colors": "lime green, dark green and white"
    }
}


# ============================================================
# TELEGRAM API
# ============================================================

TELEGRAM_API = (
    "https://api.telegram.org/"
    f"bot{TELEGRAM_BOT_TOKEN}"
)


async def telegram_request(
    method,
    data=None,
    files=None
):

    url = f"{TELEGRAM_API}/{method}"

    async with httpx.AsyncClient(
        timeout=120
    ) as client:

        response = await client.post(
            url,
            data=data,
            files=files
        )

        try:
            result = response.json()
        except Exception:

            raise RuntimeError(
                f"Telegram returned HTTP "
                f"{response.status_code}"
            )

        if not result.get("ok"):

            raise RuntimeError(
                result.get(
                    "description",
                    "Telegram API error"
                )
            )

        return result


# ============================================================
# SEND MESSAGE
# ============================================================

async def send_message(
    chat_id,
    text,
    keyboard=None
):

    data = {
        "chat_id": chat_id,
        "text": text
    }

    if keyboard:

        data["reply_markup"] = json.dumps(
            keyboard
        )

    return await telegram_request(
        "sendMessage",
        data=data
    )


# ============================================================
# SEND PHOTO
# ============================================================

async def send_photo(
    chat_id,
    image_bytes,
    caption,
    keyboard=None
):

    data = {
        "chat_id": chat_id,
        "caption": caption
    }

    if keyboard:

        data["reply_markup"] = json.dumps(
            keyboard
        )

    files = {
        "photo": (
            "wokers-ng.png",
            image_bytes,
            "image/png"
        )
    }

    return await telegram_request(
        "sendPhoto",
        data=data,
        files=files
    )


# ============================================================
# ANSWER CALLBACK
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

    return await telegram_request(
        "answerCallbackQuery",
        data=data
    )


# ============================================================
# MAIN MENU
# ============================================================

def main_menu():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "🚀 Generate Post",
                    "callback_data": "generate"
                }
            ],

            [
                {
                    "text": "🎨 Choose Color",
                    "callback_data": "colors"
                }
            ],

            [
                {
                    "text": "ℹ️ About",
                    "callback_data": "about"
                }
            ]

        ]
    }


# ============================================================
# COLOR MENU
# ============================================================

def color_menu():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "💚 Emerald",
                    "callback_data": "color:emerald"
                },

                {
                    "text": "💙 Ocean",
                    "callback_data": "color:blue"
                }
            ],

            [
                {
                    "text": "💜 Purple",
                    "callback_data": "color:purple"
                },

                {
                    "text": "🧡 Orange",
                    "callback_data": "color:orange"
                }
            ],

            [
                {
                    "text": "💛 Gold",
                    "callback_data": "color:gold"
                },

                {
                    "text": "🩵 Teal",
                    "callback_data": "color:teal"
                }
            ],

            [
                {
                    "text": "❤️ Red",
                    "callback_data": "color:red"
                },

                {
                    "text": "💚 Lime",
                    "callback_data": "color:lime"
                }
            ],

            [
                {
                    "text": "⬅️ Back",
                    "callback_data": "home"
                }
            ]

        ]
    }


# ============================================================
# PREVIEW MENU
# ============================================================

def preview_menu(
    has_image=True
):

    buttons = []

    if has_image:

        buttons.append([
            {
                "text": "📢 Post Image + Text",
                "callback_data": "publish_image"
            }
        ])

    buttons.append([
        {
            "text": "📝 Post Text Only",
            "callback_data": "publish_text"
        }
    ])

    buttons.append([
        {
            "text": "🔄 Generate Again",
            "callback_data": "generate"
        }
    ])

    buttons.append([
        {
            "text": "🎨 Change Color",
            "callback_data": "colors"
        }
    ])

    buttons.append([
        {
            "text": "❌ Cancel",
            "callback_data": "cancel"
        }
    ])

    return {
        "inline_keyboard": buttons
    }


# ============================================================
# GENERATE HAUSA POST
# ============================================================

def generate_hausa_post():

    prompt = """
You are the official AI content creator
for WOKERS NG.

Create ONE professional Telegram post
in natural Nigerian Hausa.

Choose a useful topic from:

- Technology
- Artificial Intelligence
- Digital skills
- Programming
- Website development
- Mobile app development
- Freelancing
- Online work
- Productivity
- Career development
- Useful websites
- Digital business
- Learning new skills

RULES:

- Use natural Nigerian Hausa.
- Make it easy to understand.
- Start with a strong short title.
- Use suitable emojis.
- Use short paragraphs.
- Give practical information.
- Make it engaging.
- End with a simple call to action.
- Add 3 to 5 hashtags.

SAFETY / ACCURACY:

- Do not promise guaranteed income.
- Do not invent jobs.
- Do not invent companies.
- Do not invent salaries.
- Do not invent statistics.
- Do not promote gambling.
- Do not make misleading claims.
- Do not claim that everyone will make money.

Channel:

Wokers NG
https://t.me/wokersng

Return ONLY the final Telegram post.

Do not explain your answer.
"""

    try:

        response = gemini.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt
        )

    except ClientError as error:

        if getattr(
            error,
            "code",
            None
        ) == 429:

            raise RuntimeError(
                "TEXT_QUOTA"
            )

        raise

    text = (
        response.text
        if response.text
        else ""
    ).strip()

    if not text:

        raise RuntimeError(
            "Gemini returned empty text."
        )

    # Telegram caption safe limit
    if len(text) > 1000:

        text = (
            text[:997]
            + "..."
        )

    return text


# ============================================================
# GENERATE IMAGE
# ============================================================

def generate_image(
    post_text,
    color_key
):

    theme = THEMES.get(
        color_key,
        THEMES["emerald"]
    )

    prompt = f"""
Create ONE professional social-media graphic
for WOKERS NG.

Post topic:

{post_text}

COLOR THEME:

{theme["name"]}

COLORS:

{theme["colors"]}

DESIGN:

- premium
- modern
- clean
- professional
- high quality
- attractive
- mobile friendly
- technology-focused
- Nigerian digital-skills atmosphere
- social-media ready
- 16:9 composition

Include suitable visual elements such as:

technology,
smartphone,
laptop,
AI,
coding,
digital skills,
online work.

IMPORTANT:

Do NOT use real people's faces.

Do NOT use copyrighted logos.

Do NOT create fake payment screenshots.

Do NOT create fake earnings proof.

Do NOT create fake statistics.

Do NOT promise guaranteed income.

Do NOT place the entire Telegram caption
inside the image.

Create ONE clean professional graphic.
"""

    try:

        response = gemini.models.generate_content(
            model="gemini-2.5-flash-image",
            contents=prompt,
            config=types.GenerateContentConfig(
                response_modalities=[
                    "IMAGE"
                ]
            )
        )

    except ClientError as error:

        if getattr(
            error,
            "code",
            None
        ) == 429:

            raise RuntimeError(
                "IMAGE_QUOTA"
            )

        raise

    for part in response.parts:

        inline_data = getattr(
            part,
            "inline_data",
            None
        )

        if inline_data:

            data = getattr(
                inline_data,
                "data",
                None
            )

            if data:

                if isinstance(
                    data,
                    bytes
                ):

                    return data

                return bytes(data)

    raise RuntimeError(
        "IMAGE_EMPTY"
    )


# ============================================================
# CREATE TEXT ONLY
# ============================================================

async def create_text_post(
    user_id,
    color_key
):

    try:

        text = await asyncio.to_thread(
            generate_hausa_post
        )

    except RuntimeError as error:

        if str(error) == "TEXT_QUOTA":

            raise RuntimeError(
                "Gemini text quota ya ƙare."
            )

        raise

    user_posts[user_id] = {
        "text": text,
        "image": None,
        "color": color_key
    }

    return text


# ============================================================
# CREATE FULL POST
# ============================================================

async def create_full_post(
    user_id,
    color_key
):

    # --------------------------------------------------------
    # Generate text first
    # --------------------------------------------------------

    try:

        text = await asyncio.to_thread(
            generate_hausa_post
        )

    except RuntimeError as error:

        if str(error) == "TEXT_QUOTA":

            raise RuntimeError(
                "Gemini text quota ya ƙare."
            )

        raise

    # --------------------------------------------------------
    # Generate image
    # --------------------------------------------------------

    image = None
    image_error = None

    try:

        image = await asyncio.to_thread(
            generate_image,
            text,
            color_key
        )

    except RuntimeError as error:

        if str(error) == "IMAGE_QUOTA":

            image_error = (
                "Gemini image quota ya ƙare."
            )

        elif str(error) == "IMAGE_EMPTY":

            image_error = (
                "Gemini bai dawo da image ba."
            )

        else:

            image_error = str(error)

    # --------------------------------------------------------
    # Save
    # --------------------------------------------------------

    user_posts[user_id] = {
        "text": text,
        "image": image,
        "color": color_key
    }

    return (
        text,
        image,
        image_error
    )


# ============================================================
# START MESSAGE
# ============================================================

async def send_start(
    chat_id
):

    text = """
👋 *Sannu da zuwa WOKERS NG AI BOT*

🤖 Wannan bot zai taimaka maka ka ƙirƙiri:

📝 Hausa AI post
🖼️ AI-generated image
🎨 Color themes
👀 Preview
📢 Manual posting

⚡ Babu automatic posting.

Kai ne zaka danna Generate lokacin da
kake son ƙirƙirar sabon post.

👇
"""

    await send_message(
        chat_id,
        text,
        main_menu()
    )


# ============================================================
# SHOW COLORS
# ============================================================

async def show_colors(
    chat_id
):

    await send_message(
        chat_id,
        """
🎨 *Zaɓi Color Theme*

Wannan color ɗin za a yi amfani da shi
wajen ƙirƙirar AI image.
""",
        color_menu()
    )


# ============================================================
# GENERATE FLOW
# ============================================================

async def generate_flow(
    chat_id,
    user_id,
    color_key="emerald"
):

    await send_message(
        chat_id,
        f"""
⏳ *Ana ƙirƙirar post...*

🎨 Color: {THEMES[color_key]["name"]}

🤖 Ana ƙirƙirar Hausa text...
🖼️ Ana ƙoƙarin ƙirƙirar image...

Ka ɗan jira.
"""
    )

    try:

        text, image, image_error = (
            await create_full_post(
                user_id,
                color_key
            )
        )

        # ----------------------------------------------------
        # Image available
        # ----------------------------------------------------

        if image:

            caption = (
                "👀 *WOKERS NG — PREVIEW*\n\n"
                f"🎨 {THEMES[color_key]['name']}\n\n"
                + text
            )

            if len(caption) > 1000:

                caption = (
                    caption[:997]
                    + "..."
                )

            await send_photo(
                chat_id,
                image,
                caption,
                preview_menu(True)
            )

            return

        # ----------------------------------------------------
        # Image failed / quota
        # ----------------------------------------------------

        if image_error:

            await send_message(
                chat_id,
                f"""
⚠️ *Image ba ta samu ba.*

{image_error}

Amma ✅ Hausa post ɗin an ƙirƙira shi.

👇 Ga zaɓinka:
""",
                preview_menu(False)
            )

            await send_message(
                chat_id,
                text
            )

            return

    except RuntimeError as error:

        await send_message(
            chat_id,
            f"""
❌ *An samu matsala.*

{str(error)}

Ka sake gwadawa daga baya.
""",
            main_menu()
        )

    except Exception as error:

        logger.exception(
            "Generate error: %s",
            error
        )

        await send_message(
            chat_id,
            """
❌ An samu matsala wajen Generate.

Ka sake gwadawa.
""",
            main_menu()
        )


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

    user = callback.get(
        "from",
        {}
    )

    user_id = user.get(
        "id"
    )

    # --------------------------------------------------------
    # Answer callback
    # --------------------------------------------------------

    try:

        await answer_callback(
            callback_id
        )

    except Exception:
        pass

    # --------------------------------------------------------
    # HOME
    # --------------------------------------------------------

    if data == "home":

        await send_start(
            chat_id
        )

        return

    # --------------------------------------------------------
    # ABOUT
    # --------------------------------------------------------

    if data == "about":

        await send_message(
            chat_id,
            """
ℹ️ *WOKERS NG AI BOT*

🤖 Gemini AI
📝 Hausa content
🖼️ AI image generation
🎨 Multiple colors
👀 Preview system
📢 Manual publishing

❌ Automatic posting: OFF

Kai ne kake sarrafa lokacin Generate
da lokacin Post.
""",
            main_menu()
        )

        return

    # --------------------------------------------------------
    # COLORS
    # --------------------------------------------------------

    if data == "colors":

        await show_colors(
            chat_id
        )

        return

    # --------------------------------------------------------
    # COLOR SELECTED
    # --------------------------------------------------------

    if data.startswith(
        "color:"
    ):

        color_key = data.split(
            ":",
            1
        )[1]

        if color_key not in THEMES:

            color_key = "emerald"

        # Save selected color temporarily
        user_posts[user_id] = {
            "selected_color": color_key
        }

        await send_message(
            chat_id,
            f"""
✅ *An zaɓi Color*

🎨 {THEMES[color_key]["name"]}

Yanzu danna Generate Post.
""",
            {
                "inline_keyboard": [
                    [
                        {
                            "text": "🚀 Generate Post",
                            "callback_data": "generate"
                        }
                    ],
                    [
                        {
                            "text": "🎨 Change Color",
                            "callback_data": "colors"
                        }
                    ],
                    [
                        {
                            "text": "⬅️ Home",
                            "callback_data": "home"
                        }
                    ]
                ]
            }
        )

        return

    # --------------------------------------------------------
    # GENERATE
    # --------------------------------------------------------

    if data == "generate":

        existing = user_posts.get(
            user_id,
            {}
        )

        color_key = existing.get(
            "selected_color",
            existing.get(
                "color",
                "emerald"
            )
        )

        # Clear old post but retain color
        user_posts[user_id] = {
            "selected_color": color_key
        }

        await generate_flow(
            chat_id,
            user_id,
            color_key
        )

        return

    # --------------------------------------------------------
    # GENERATE AGAIN
    # --------------------------------------------------------

    if data == "generate_again":

        existing = user_posts.get(
            user_id,
            {}
        )

        color_key = existing.get(
            "color",
            existing.get(
                "selected_color",
                "emerald"
            )
        )

        await generate_flow(
            chat_id,
            user_id,
            color_key
        )

        return

    # --------------------------------------------------------
    # PUBLISH IMAGE + TEXT
    # --------------------------------------------------------

    if data == "publish_image":

        post = user_posts.get(
            user_id
        )

        if not post:

            await send_message(
                chat_id,
                "⚠️ Babu preview da za a aika.",
                main_menu()
            )

            return

        image = post.get(
            "image"
        )

        text = post.get(
            "text",
            ""
        )

        if not image:

            await send_message(
                chat_id,
                "⚠️ Babu image. Yi Post Text Only.",
                preview_menu(False)
            )

            return

        try:

            await send_photo(
                TELEGRAM_CHANNEL,
                image,
                text
            )

            user_posts.pop(
                user_id,
                None
            )

            await send_message(
                chat_id,
                f"""
✅ *An aika post!*

📢 Channel:
{TELEGRAM_CHANNEL}

🎨 Color:
{THEMES.get(
    post.get("color", "emerald"),
    THEMES["emerald"]
)["name"]}
""",
                main_menu()
            )

        except Exception as error:

            logger.exception(
                "Publish image error: %s",
                error
            )

            await send_message(
                chat_id,
                """
❌ An kasa aika image zuwa channel.

Ka tabbatar bot ɗin yana Admin
a cikin @wokersng kuma yana da
permission na posting.
""",
                preview_menu(True)
            )

        return

    # --------------------------------------------------------
    # PUBLISH TEXT ONLY
    # --------------------------------------------------------

    if data == "publish_text":

        post = user_posts.get(
            user_id
        )

        if not post:

            await send_message(
                chat_id,
                "⚠️ Babu post da za a aika.",
                main_menu()
            )

            return

        text = post.get(
            "text",
            ""
        )

        if not text:

            await send_message(
                chat_id,
                "⚠️ Babu text.",
                main_menu()
            )

            return

        try:

            await send_message(
                TELEGRAM_CHANNEL,
                text
            )

            user_posts.pop(
                user_id,
                None
            )

            await send_message(
                chat_id,
                f"""
✅ *Text post ya shiga channel!*

📢 {TELEGRAM_CHANNEL}
""",
                main_menu()
            )

        except Exception as error:

            logger.exception(
                "Publish text error: %s",
                error
            )

            await send_message(
                chat_id,
                """
❌ An kasa aika text zuwa channel.

Ka tabbatar bot ɗin yana Admin
a cikin @wokersng.
""",
                preview_menu(False)
            )

        return

    # --------------------------------------------------------
    # CANCEL
    # --------------------------------------------------------

    if data == "cancel":

        user_posts.pop(
            user_id,
            None
        )

        await send_message(
            chat_id,
            """
❌ *An soke.*

Ba a aika post zuwa channel ba.
""",
            main_menu()
        )

        return


# ============================================================
# PROCESS TELEGRAM UPDATE
# ============================================================

async def process_update(
    update
):

    try:

        # ----------------------------------------------------
        # Callback
        # ----------------------------------------------------

        if "callback_query" in update:

            await handle_callback(
                update["callback_query"]
            )

            return

        # ----------------------------------------------------
        # Message
        # ----------------------------------------------------

        if "message" not in update:

            return

        message = update["message"]

        chat = message.get(
            "chat",
            {}
        )

        chat_id = chat.get(
            "id"
        )

        text = message.get(
            "text",
            ""
        ).strip()

        if not chat_id:

            return

        # ----------------------------------------------------
        # START
        # ----------------------------------------------------

        if text == "/start":

            await send_start(
                chat_id
            )

            return

        # ----------------------------------------------------
        # GENERATE
        # ----------------------------------------------------

        if text == "/generate":

            existing = user_posts.get(
                chat_id,
                {}
            )

            color_key = existing.get(
                "selected_color",
                "emerald"
            )

            await generate_flow(
                chat_id,
                chat_id,
                color_key
            )

            return

        # ----------------------------------------------------
        # HELP
        # ----------------------------------------------------

        if text == "/help":

            await send_message(
                chat_id,
                """
📚 *Commands*

/start - Open bot
/generate - Generate post
/help - Show help

Ko kana amfani da buttons,
komai yana nan a menu.
""",
                main_menu()
            )

            return

        # ----------------------------------------------------
        # UNKNOWN
        # ----------------------------------------------------

        await send_message(
            chat_id,
            """
Ban gane command ɗin ba.

Danna /start domin buɗe menu.
""",
            main_menu()
        )

    except Exception as error:

        logger.exception(
            "Update processing error: %s",
            error
        )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/telegram-webhook",
    methods=["POST"]
)
def telegram_webhook():

    if WEBHOOK_SECRET:

        incoming_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if incoming_secret != WEBHOOK_SECRET:

            return jsonify({
                "ok": False,
                "error": "Unauthorized"
            }), 403

    update = request.get_json(
        silent=True
    )

    if not update:

        return jsonify({
            "ok": True
        })

    threading.Thread(
        target=lambda: asyncio.run(
            process_update(update)
        ),
        daemon=True
    ).start()

    return jsonify({
        "ok": True
    })


# ============================================================
# HEALTH
# ============================================================

@app.route("/")
def home():

    return jsonify({
        "status": "online",
        "service": "Wokers NG Manual AI Bot",
        "automatic_posting": False,
        "channel": TELEGRAM_CHANNEL
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# ============================================================
# SET WEBHOOK
# ============================================================

async def set_webhook():

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

        data["secret_token"] = WEBHOOK_SECRET

    try:

        result = await telegram_request(
            "setWebhook",
            data=data
        )

        logger.info(
            "Webhook configured: %s",
            result.get("ok")
        )

    except Exception as error:

        logger.exception(
            "Webhook setup failed: %s",
            error
        )


# ============================================================
# START
# ============================================================

def main():

    # --------------------------------------------------------
    # Configure Telegram webhook
    # --------------------------------------------------------

    asyncio.run(
        set_webhook()
    )

    # --------------------------------------------------------
    # Render port
    # --------------------------------------------------------

    port = int(
        os.getenv(
            "PORT",
            "10000"
        )
    )

    logger.info(
        "=========================================="
    )

    logger.info(
        "WOKERS NG MANUAL AI BOT"
    )

    logger.info(
        "Automatic posting: OFF"
    )

    logger.info(
        "Channel: %s",
        TELEGRAM_CHANNEL
    )

    logger.info(
        "=========================================="
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# ============================================================
# RUN
# ============================================================

if __name__ == "__main__":

    main()