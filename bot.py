import os
import time
import random
import asyncio
import logging
import threading

import httpx
from flask import Flask, jsonify
from google import genai
from google.genai import types


# =========================================================
# WOKERS NG TELEGRAM AUTO POST BOT
# =========================================================

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(message)s"
)

logger = logging.getLogger("wokers-ng")


# =========================================================
# ENVIRONMENT VARIABLES
# =========================================================

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "").strip()

TELEGRAM_CHANNEL = os.getenv(
    "TELEGRAM_CHANNEL",
    "@wokersng"
).strip()

POST_INTERVAL_MINUTES = int(
    os.getenv("POST_INTERVAL_MINUTES", "120")
)

POST_ON_START = os.getenv(
    "POST_ON_START",
    "true"
).lower() == "true"


# =========================================================
# VALIDATE CONFIG
# =========================================================

if not TELEGRAM_BOT_TOKEN:
    raise RuntimeError(
        "TELEGRAM_BOT_TOKEN is missing"
    )

if not GEMINI_API_KEY:
    raise RuntimeError(
        "GEMINI_API_KEY is missing"
    )


# =========================================================
# GEMINI CLIENT
# =========================================================

gemini = genai.Client(
    api_key=GEMINI_API_KEY
)


# =========================================================
# FLASK SERVER
# Render Web Service needs HTTP port.
# =========================================================

app = Flask(__name__)


@app.get("/")
def home():
    return jsonify({
        "status": "online",
        "bot": "Wokers NG Auto Post Bot",
        "channel": TELEGRAM_CHANNEL,
        "interval_minutes": POST_INTERVAL_MINUTES
    })


@app.get("/health")
def health():
    return jsonify({
        "status": "ok"
    })


def start_web_server():
    port = int(
        os.getenv("PORT", "10000")
    )

    app.run(
        host="0.0.0.0",
        port=port,
        debug=False,
        use_reloader=False
    )


# =========================================================
# COLOR THEMES
# =========================================================

THEMES = [
    {
        "name": "Emerald",
        "colors": "emerald green, white and gold"
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


# =========================================================
# GENERATE HAUSA POST
# =========================================================

def generate_post():

    prompt = """
You are the official Hausa content writer
for Wokers NG.

Create ONE useful Telegram post in natural
Nigerian Hausa.

Topics can include:

- digital skills
- technology
- online work
- freelancing
- AI tools
- website development
- app development
- learning
- productivity
- career advice
- legitimate opportunities

RULES:

1. Write clear Nigerian Hausa.
2. Make it useful and interesting.
3. Use emojis naturally.
4. Give the post a short attractive title.
5. Use short paragraphs.
6. Finish with a simple call to action.
7. Add 3 to 5 relevant hashtags.
8. Never promise guaranteed income.
9. Never invent fake companies or jobs.
10. Never invent salaries or statistics.
11. Never promote gambling or betting.
12. Never create misleading claims.

Return ONLY the final Telegram post.

Do not explain your answer.
"""

    response = gemini.models.generate_content(
        model="gemini-2.5-flash",
        contents=prompt
    )

    text = (response.text or "").strip()

    if not text:
        raise RuntimeError(
            "Gemini returned empty text"
        )

    # Telegram caption limit protection
    if len(text) > 1000:
        text = text[:997] + "..."

    return text


# =========================================================
# GENERATE IMAGE
# =========================================================

def generate_image(post_text, theme):

    prompt = f"""
Create a professional social-media graphic
for a Nigerian digital technology channel called:

WOKERS NG

The visual should represent:

digital skills,
technology,
online work,
learning,
AI,
freelancing,
website/app development.

Theme:

{theme["name"]}

Main colors:

{theme["colors"]}

Style:

- premium
- modern
- clean
- professional
- high quality
- attractive
- mobile-friendly
- social-media design
- strong visual hierarchy
- professional lighting
- modern Nigerian digital-work atmosphere

Do NOT use a real person's face.

Do NOT use copyrighted logos.

Do NOT show fake money screenshots.

Do NOT show fake statistics.

Do NOT make guaranteed-income claims.

The image should visually match this Hausa Telegram post:

{post_text}

Do not put the whole post inside the image.

Create a clean 16:9 social-media graphic.
"""

    response = gemini.models.generate_content(
        model="gemini-2.5-flash-image",
        contents=prompt,
        config=types.GenerateContentConfig(
            response_modalities=["IMAGE"],
            response_format={
                "image": {
                    "aspect_ratio": "16:9"
                }
            }
        )
    )

    for part in response.parts:

        if getattr(part, "inline_data", None):

            image = part.as_image()

            if image is None:
                continue

            # Convert generated image to PNG bytes
            import io

            buffer = io.BytesIO()

            image.save(
                buffer,
                format="PNG"
            )

            return buffer.getvalue()

    raise RuntimeError(
        "Gemini did not return an image"
    )


# =========================================================
# TELEGRAM REQUEST
# =========================================================

async def telegram_request(
    method,
    data=None,
    files=None
):

    url = (
        f"https://api.telegram.org/"
        f"bot{TELEGRAM_BOT_TOKEN}/"
        f"{method}"
    )

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
                f"Telegram error: {result}"
            )

        return result


# =========================================================
# SEND POST TO CHANNEL
# =========================================================

async def send_to_telegram(
    image_bytes,
    caption
):

    data = {
        "chat_id": TELEGRAM_CHANNEL,
        "caption": caption
    }

    files = {
        "photo": (
            "wokers-ng.png",
            image_bytes,
            "image/png"
        )
    }

    result = await telegram_request(
        "sendPhoto",
        data=data,
        files=files
    )

    return result


# =========================================================
# CREATE ONE POST
# =========================================================

async def create_post():

    logger.info(
        "Creating new Wokers NG post..."
    )

    # Choose random color
    theme = random.choice(THEMES)

    logger.info(
        "Selected theme: %s",
        theme["name"]
    )

    # Generate Hausa caption
    post_text = generate_post()

    logger.info(
        "Hausa caption generated."
    )

    # Generate image
    image_bytes = generate_image(
        post_text,
        theme
    )

    logger.info(
        "Image generated."
    )

    # Send to Telegram
    await send_to_telegram(
        image_bytes,
        post_text
    )

    logger.info(
        "POST SENT SUCCESSFULLY -> %s",
        TELEGRAM_CHANNEL
    )


# =========================================================
# ERROR-SAFE POST
# =========================================================

async def safe_create_post():

    try:

        await create_post()

    except Exception as error:

        logger.exception(
            "POST FAILED: %s",
            error
        )


# =========================================================
# 2-HOUR SCHEDULER
# =========================================================

async def scheduler():

    logger.info(
        "Wokers NG scheduler started."
    )

    logger.info(
        "Interval: %s minutes",
        POST_INTERVAL_MINUTES
    )

    # First post after deployment
    if POST_ON_START:

        await safe_create_post()

    while True:

        logger.info(
            "Waiting %s minutes...",
            POST_INTERVAL_MINUTES
        )

        await asyncio.sleep(
            POST_INTERVAL_MINUTES * 60
        )

        await safe_create_post()


# =========================================================
# MAIN
# =========================================================

def main():

    web_thread = threading.Thread(
        target=start_web_server,
        daemon=True
    )

    web_thread.start()

    logger.info(
        "Web health server started."
    )

    asyncio.run(
        scheduler()
    )


if __name__ == "__main__":
    main()