import os
import io
import asyncio
import logging
import threading
import secrets

import httpx
from flask import Flask, request, jsonify
from google import genai
from google.genai import types


# ============================================================
# WOKERS NG - MANUAL AI TELEGRAM POST GENERATOR
# ============================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("WOKERS-NG")


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
# TEMPORARY USER DATA
# ============================================================

user_posts = {}


# ============================================================
# COLOR THEMES
# ============================================================

THEMES = [
    {
        "name": "Emerald",
        "colors": "emerald green, white and soft gold"
    },
    {
        "name": "Ocean",
        "colors": "deep blue, cyan and white"
    },
    {
        "name": "Royal",
        "colors": "royal blue, purple and white"
    },
    {
        "name": "Sunset",
        "colors": "orange, coral and purple"
    },
    {
        "name": "Fresh",
        "colors": "lime green, dark green and white"
    },
    {
        "name": "Premium",
        "colors": "black, gold and white"
    },
    {
        "name": "Sky",
        "colors": "sky blue, navy and white"
    },
    {
        "name": "Modern",
        "colors": "teal, navy and white"
    }
]


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

        result = response.json()

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

        data["reply_markup"] = (
            __import__("json").dumps(
                keyboard
            )
        )

    return await telegram_request(
        "sendMessage",
        data=data
    )


# ============================================================
# EDIT MESSAGE
# ============================================================

async def edit_message(
    chat_id,
    message_id,
    text,
    keyboard=None
):

    data = {
        "chat_id": chat_id,
        "message_id": message_id,
        "text": text
    }

    if keyboard:

        data["reply_markup"] = (
            __import__("json").dumps(
                keyboard
            )
        )

    return await telegram_request(
        "editMessageText",
        data=data
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
# SEND PHOTO
# ============================================================

async def send_photo(
    chat_id,
    image_bytes,
    caption
):

    data = {
        "chat_id": chat_id,
        "caption": caption
    }

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
# MAIN MENU
# ============================================================

def main_keyboard():

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
                    "text": "ℹ️ About",
                    "callback_data": "about"
                }
            ]

        ]
    }


# ============================================================
# PREVIEW MENU
# ============================================================

def preview_keyboard():

    return {
        "inline_keyboard": [

            [
                {
                    "text": "📢 Post to Channel",
                    "callback_data": "publish"
                }
            ],

            [
                {
                    "text": "🔄 Generate Again",
                    "callback_data": "generate"
                }
            ],

            [
                {
                    "text": "❌ Cancel",
                    "callback_data": "cancel"
                }
            ]

        ]
    }


# ============================================================
# GENERATE HAUSA POST
# ============================================================

def generate_hausa_post():

    prompt = """
You are the official AI content creator for Wokers NG.

Create ONE high-quality Telegram post in natural
Nigerian Hausa.

Choose an interesting topic from:

- technology
- AI
- digital skills
- website development
- mobile app development
- programming
- freelancing
- online work
- productivity
- career development
- useful websites
- digital business
- learning new skills

RULES:

1. Use natural Nigerian Hausa.
2. Make the post useful and interesting.
3. Start with a short attractive title.
4. Use emojis naturally.
5. Use short paragraphs.
6. Give practical information.
7. End with a simple call to action.
8. Add 3 to 5 relevant hashtags.
9. Do not promise guaranteed income.
10. Do not invent jobs.
11. Do not invent companies.
12. Do not invent salary figures.
13. Do not invent statistics.
14. Do not promote gambling or betting.
15. Do not make misleading claims.

Channel:

Wokers NG
https://t.me/wokersng

Return ONLY the final Telegram caption.

Do not explain anything.
"""

    response = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    text = (
        response.text
        if response.text
        else ""
    ).strip()

    if not text:

        raise RuntimeError(
            "Gemini returned empty text."
        )

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
    theme
):

    prompt = f"""
Create a professional social media graphic for:

WOKERS NG

This is a Nigerian technology and digital-skills
Telegram channel.

Create an image matching this post:

{post_text}

COLOR THEME:

{theme["name"]}

COLORS:

{theme["colors"]}

STYLE:

- premium
- modern
- clean
- professional
- high quality
- attractive
- mobile friendly
- social media design
- technology atmosphere
- Nigerian digital-work atmosphere

Use suitable visual elements such as:

smartphone,
laptop,
AI,
coding,
technology,
digital skills,
online work.

IMPORTANT:

Do NOT use real people's faces.

Do NOT use copyrighted logos.

Do NOT create fake payment screenshots.

Do NOT create fake statistics.

Do NOT create fake earnings proof.

Do NOT make guaranteed-income claims.

Do NOT put the whole caption inside the image.

Create ONE clean 16:9 social media graphic.
"""

    response = gemini.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"]
        )
    )

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
        "Gemini did not return image."
    )


# ============================================================
# CREATE POST
# ============================================================

async def create_post_for_user(
    user_id
):

    theme = secrets.choice(
        THEMES
    )

    logger.info(
        "Generating post for user %s",
        user_id
    )

    # Generate text
    post_text = await asyncio.to_thread(
        generate_hausa_post
    )

    logger.info(
        "Hausa text generated."
    )

    # Generate image
    image_bytes = await asyncio.to_thread(
        generate_image,
        post_text,
        theme
    )

    logger.info(
        "Image generated."
    )

    # Save temporarily for this user
    user_posts[user_id] = {
        "text": post_text,
        "image": image_bytes,
        "theme": theme["name"]
    }

    return (
        post_text,
        image_bytes,
        theme["name"]
    )


# ============================================================
# SEND PREVIEW
# ============================================================

async def send_preview(
    chat_id,
    post_text,
    image_bytes,
    theme
):

    caption = (
        "✨ WOKERS NG AI POST PREVIEW\n\n"
        f"🎨 Theme: {theme}\n\n"
        "👇 Duba post ɗin kafin ka aika.\n\n"
        + post_text
    )

    # Telegram caption limit
    if len(caption) > 1000:

        caption = caption[:997] + "..."

    return await send_photo(
        chat_id,
        image_bytes,
        caption
    )


# ============================================================
# HANDLE /START
# ============================================================

async def handle_start(
    chat_id
):

    text = """
👋 Sannu da zuwa WOKERS NG AI POST BOT

Wannan bot zai taimaka maka ka ƙirƙiri:

🤖 AI-generated Hausa post
🖼️ AI-generated image
🎨 Different color themes

Ba zai yi automatic posting ba.

Kai ne zaka zaɓi lokacin da za a ƙirƙiri
post da lokacin da za a aika shi zuwa channel.

👇 Danna button ɗin ƙasa:
"""

    await send_message(
        chat_id,
        text,
        main_keyboard()
    )


# ============================================================
# HANDLE CALLBACK
# ============================================================

async def handle_callback(
    callback
):

    callback_id = callback["id"]

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

    message_id = message.get(
        "message_id"
    )

    user_id = (
        callback.get(
            "from",
            {}
        ).get(
            "id"
        )
    )

    # --------------------------------------------------------
    # GENERATE
    # --------------------------------------------------------

    if data == "generate":

        await answer_callback(
            callback_id,
            "🤖 Ana ƙirƙirar post..."
        )

        try:

            await edit_message(
                chat_id,
                message_id,
                "⏳ AI na ƙirƙirar Hausa post + image...\n\n"
                "Ka ɗan jira."
            )

            post_text, image_bytes, theme = (
                await create_post_for_user(
                    user_id
                )
            )

            await send_preview(
                chat_id,
                post_text,
                image_bytes,
                theme
            )

            await send_message(
                chat_id,
                "✅ An gama!\n\n"
                "Ka zaɓi abin da kake so:",
                preview_keyboard()
            )

        except Exception as error:

            logger.exception(
                "Generation error: %s",
                error
            )

            await send_message(
                chat_id,
                "❌ An samu matsala wajen Generate.\n\n"
                f"Error: {str(error)[:300]}",
                main_keyboard()
            )

        return

    # --------------------------------------------------------
    # PUBLISH
    # --------------------------------------------------------

    if data == "publish":

        await answer_callback(
            callback_id,
            "📢 Ana aika post..."
        )

        post = user_posts.get(
            user_id
        )

        if not post:

            await send_message(
                chat_id,
                "⚠️ Babu post da aka shirya.\n"
                "Danna Generate Post.",
                main_keyboard()
            )

            return

        try:

            await send_photo(
                TELEGRAM_CHANNEL,
                post["image"],
                post["text"]
            )

            # Delete temporary data
            user_posts.pop(
                user_id,
                None
            )

            await send_message(
                chat_id,
                "✅ POST YA SHIGA CHANNEL!\n\n"
                f"📢 {TELEGRAM_CHANNEL}\n"
                f"🎨 Theme: {post['theme']}",
                main_keyboard()
            )

        except Exception as error:

            logger.exception(
                "Publish error: %s",
                error
            )

            await send_message(
                chat_id,
                "❌ An kasa aika post zuwa channel.\n\n"
                "Ka tabbatar bot ɗin yana Admin "
                "a channel kuma yana da permission "
                "na posting.",
                main_keyboard()
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

        await answer_callback(
            callback_id,
            "An soke."
        )

        await send_message(
            chat_id,
            "❌ An soke post ɗin.\n\n"
            "Ba a aika komai zuwa channel ba.",
            main_keyboard()
        )

        return

    # --------------------------------------------------------
    # ABOUT
    # --------------------------------------------------------

    if data == "about":

        await answer_callback(
            callback_id
        )

        await send_message(
            chat_id,
            """
ℹ️ WOKERS NG AI POST BOT

🤖 Gemini AI
📝 Hausa content
🖼️ AI image generation
🎨 Multiple themes
📢 Manual channel publishing

Babu automatic posting.

Kai ne kake sarrafa komai.
""",
            main_keyboard()
        )

        return


# ============================================================
# TELEGRAM UPDATE HANDLER
# ============================================================

async def process_update(
    update
):

    # --------------------------------------------------------
    # Normal message
    # --------------------------------------------------------

    if "message" in update:

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
        )

        if text.startswith(
            "/start"
        ):

            await handle_start(
                chat_id
            )

        elif text.startswith(
            "/generate"
        ):

            # Same action as Generate button
            await send_message(
                chat_id,
                "🤖 Ana fara Generate..."
            )

            try:

                user_id = chat_id

                post_text, image_bytes, theme = (
                    await create_post_for_user(
                        user_id
                    )
                )

                await send_preview(
                    chat_id,
                    post_text,
                    image_bytes,
                    theme
                )

                await send_message(
                    chat_id,
                    "✅ An gama.\n"
                    "Me za mu yi da post ɗin?",
                    preview_keyboard()
                )

            except Exception as error:

                logger.exception(
                    "Generate command error: %s",
                    error
                )

                await send_message(
                    chat_id,
                    "❌ Generate ya kasa.\n\n"
                    "Ka sake gwadawa.",
                    main_keyboard()
                )

        return

    # --------------------------------------------------------
    # Callback query
    # --------------------------------------------------------

    if "callback_query" in update:

        await handle_callback(
            update["callback_query"]
        )


# ============================================================
# WEBHOOK
# ============================================================

@app.route(
    "/telegram-webhook",
    methods=["POST"]
)
def telegram_webhook():

    # Optional webhook secret
    if WEBHOOK_SECRET:

        incoming_secret = request.headers.get(
            "X-Telegram-Bot-Api-Secret-Token",
            ""
        )

        if incoming_secret != WEBHOOK_SECRET:

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

    # Process in background
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
        "bot": "Wokers NG Manual AI Bot",
        "automatic_posting": False,
        "channel": TELEGRAM_CHANNEL
    })


@app.route("/health")
def health():

    return jsonify({
        "status": "healthy"
    })


# ============================================================
# SET TELEGRAM WEBHOOK
# ============================================================

async def set_webhook():

    if not RENDER_EXTERNAL_URL:

        logger.warning(
            "RENDER_EXTERNAL_URL is not set."
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
            "Telegram webhook configured: %s",
            result.get("ok")
        )

    except Exception as error:

        logger.exception(
            "Failed to set Telegram webhook: %s",
            error
        )


# ============================================================
# START SERVER
# ============================================================

def main():

    # Set webhook
    asyncio.run(
        set_webhook()
    )

    # Render port
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
        "WOKERS NG MANUAL AI BOT STARTED"
    )

    logger.info(
        "Automatic posting: DISABLED"
    )

    logger.info(
        "Telegram channel: %s",
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