import os
import json
import time
import csv
from datetime import datetime, timezone
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load hidden keys from .env
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN")
HOST_URL = os.getenv("HOST_URL", "http://localhost:8000") 

app = FastAPI()
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
LOG_FILE = os.path.join(BASE_DIR, "run.jsonl")
PROFILE_FILE = os.path.join(BASE_DIR, "user_profiles.csv")
PROFILE_FIELDS = [
    "created_at", "chat_id", "telegram_user_id", "username", "first_name",
    "last_name", "name", "age", "photo_path", "latitude", "longitude",
    "phone_number"
]

# Memory settings
MAX_HISTORY_MESSAGES = 10    # Keeps last 3 turns (3 user + 3 assistant)
INACTIVITY_TIMEOUT = 360    # Clear memory after 2 minutes of silence

# Stores chat memory and last message timestamp per chat_id:
# { chat_id: {"messages": [...], "last_seen": float} }
CONVERSATION_HISTORY = {}
ONBOARDING = {}

client = AsyncOpenAI(
    api_key=AIPIPE_TOKEN,
    base_url="https://aipipe.org/openai/v1"
)

async def telegram_api(method, payload=None):
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/{method}"
    async with httpx.AsyncClient() as http_client:
        response = await http_client.post(url, json=payload or {})
        response.raise_for_status()
        result = response.json()
        if not result.get("ok"):
            raise RuntimeError(result.get("description", f"Telegram {method} failed"))
        return result["result"]

async def send_telegram_message(chat_id, text, reply_markup=None):
    payload = {"chat_id": chat_id, "text": text}
    if reply_markup is not None:
        payload["reply_markup"] = reply_markup
    await telegram_api("sendMessage", payload)

def save_profile(profile):
    file_exists = os.path.exists(PROFILE_FILE) and os.path.getsize(PROFILE_FILE) > 0
    with open(PROFILE_FILE, "a", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(file, fieldnames=PROFILE_FIELDS)
        if not file_exists:
            writer.writeheader()
        writer.writerow({field: profile.get(field, "") for field in PROFILE_FIELDS})

async def download_photo(photo_sizes, chat_id):
    photo = photo_sizes[-1]
    file_info = await telegram_api("getFile", {"file_id": photo["file_id"]})
    file_path = file_info["file_path"]
    photo_dir = os.path.join(BASE_DIR, "user_photos")
    os.makedirs(photo_dir, exist_ok=True)
    extension = os.path.splitext(file_path)[1] or ".jpg"
    local_path = os.path.join(photo_dir, f"{chat_id}_{int(time.time())}{extension}")
    download_url = f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}"
    async with httpx.AsyncClient() as http_client:
        response = await http_client.get(download_url)
        response.raise_for_status()
        with open(local_path, "wb") as file:
            file.write(response.content)
    return os.path.relpath(local_path, BASE_DIR)

def location_keyboard():
    return {"keyboard": [[{"text": "Share my location", "request_location": True}]], "resize_keyboard": True, "one_time_keyboard": True}

def contact_keyboard():
    return {"keyboard": [[{"text": "Share my phone number", "request_contact": True}]], "resize_keyboard": True, "one_time_keyboard": True}

async def begin_onboarding(message):
    chat_id = message["chat"]["id"]
    user = message.get("from", {})
    ONBOARDING[chat_id] = {
        "step": "name", "chat_id": chat_id,
        "telegram_user_id": user.get("id", ""), "username": user.get("username", ""),
        "first_name": user.get("first_name", ""), "last_name": user.get("last_name", "")
    }
    await send_telegram_message(chat_id, "Welcome. What is your name?")

async def handle_onboarding(message):
    chat_id = message["chat"]["id"]
    profile = ONBOARDING[chat_id]
    step = profile["step"]

    if step == "name":
        name = message.get("text", "").strip()
        if not name:
            await send_telegram_message(chat_id, "Please send your name as text.")
            return True
        profile.update(name=name, step="age")
        await send_telegram_message(chat_id, "Thanks. How old are you? Send your age as a number.")
    elif step == "age":
        try:
            age = int(message.get("text", "").strip())
            if not 0 < age < 130:
                raise ValueError
        except (TypeError, ValueError):
            await send_telegram_message(chat_id, "Please send a valid age as a whole number.")
            return True
        profile.update(age=age, step="photo")
        await send_telegram_message(chat_id, "Please send a profile picture.")
    elif step == "photo":
        if "photo" not in message:
            await send_telegram_message(chat_id, "Please send an image using Telegram's photo attachment.")
            return True
        profile.update(photo_path=await download_photo(message["photo"], chat_id), step="location")
        await send_telegram_message(chat_id, "Now share your location using the button below.", location_keyboard())
    elif step == "location":
        location = message.get("location")
        if not location:
            await send_telegram_message(chat_id, "Please use the 'Share my location' button; do not type it manually.", location_keyboard())
            return True
        profile.update(latitude=location["latitude"], longitude=location["longitude"], step="contact")
        await send_telegram_message(chat_id, "Finally, share your phone number using the button below.", contact_keyboard())
    elif step == "contact":
        contact = message.get("contact")
        if not contact or contact.get("user_id") != profile.get("telegram_user_id"):
            await send_telegram_message(chat_id, "Please use the 'Share my phone number' button for this Telegram account.", contact_keyboard())
            return True
        profile["phone_number"] = contact.get("phone_number", "")
        profile["created_at"] = datetime.now(timezone.utc).isoformat()
        save_profile(profile)
        del ONBOARDING[chat_id]
        await send_telegram_message(chat_id, "Your information has been saved. You can now ask data analysis questions.", {"remove_keyboard": True})
    return True

def log_interaction(chat_id, query, response_data):
    """Saves the interaction to the local JSONL log file."""
    log_entry = {"chat_id": chat_id, "query": query, "response": response_data}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receives incoming messages from Telegram."""
    update = await request.json()

    message = update.get("message")
    if message:
        chat_id = message["chat"]["id"]
        user_message = message.get("text", "").strip()

        if user_message == "/start":
            await begin_onboarding(message)
            return {"status": "ok"}

        if chat_id in ONBOARDING:
            try:
                await handle_onboarding(message)
            except Exception as error:
                print(f"Error during onboarding: {error}")
                await send_telegram_message(chat_id, "Something went wrong while saving that information. Please try again or send /start to restart.")
            return {"status": "ok"}
    
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        user_message = update["message"]["text"]
        log_url = f"{HOST_URL}/run.jsonl"
        current_time = time.time()
        
        # 1. Initialize or Reset Memory based on Inactivity
        if chat_id not in CONVERSATION_HISTORY:
            CONVERSATION_HISTORY[chat_id] = {"messages": [], "last_seen": current_time}
            
        # Reset memory if more than 2 minutes have passed since last message
        if current_time - CONVERSATION_HISTORY[chat_id]["last_seen"] > INACTIVITY_TIMEOUT:
            CONVERSATION_HISTORY[chat_id]["messages"] = []
            
        CONVERSATION_HISTORY[chat_id]["last_seen"] = current_time

        # 2. Append new user message
        CONVERSATION_HISTORY[chat_id]["messages"].append({"role": "user", "content": user_message})

        # 3. Apply Sliding Window (keep only recent N messages)
        if len(CONVERSATION_HISTORY[chat_id]["messages"]) > MAX_HISTORY_MESSAGES:
            CONVERSATION_HISTORY[chat_id]["messages"] = CONVERSATION_HISTORY[chat_id]["messages"][-MAX_HISTORY_MESSAGES:]

        system_prompt = f"""
            You are an expert, precise data analysis AI agent.

            CORE KNOWLEDGE & RETRIEVAL DIRECTIVE:
            1. When asked about public statistics, government data (MOSPI, RBI, Census, NITI Aayog, WHO, World Bank, SRS, etc.), or real-world facts, ALWAYS provide the exact answer using the latest available dataset from your pre-trained knowledge base or conversation history.
            2. NEVER output "Data not available", "cannot access live data", or "insufficient information" UNLESS the user explicitly asks you to verify if live internet access is enabled or explicitly allows a "data not available" output.
            3. Treat all statistical/analytical queries as requests to evaluate against the most recent recorded official data in your knowledge base.
            4. Process queries in any language (English, Hindi, etc.) seamlessly.

            CRITICAL OUTPUT CONTRACT:
            Your response MUST be EXACTLY ONE valid raw JSON object with PRECISELY TWO top-level keys:
            1. "answer": The answer payload, matching the EXACT key structure and data type requested in the user's prompt.
            2. "log_url": "{log_url}"

            EXAMPLE FORMAT:
            If the user asks: "Which state has the lowest literacy rate? Format: {{"answer": {{"state": "<name>"}}}}"
            Your output MUST be EXACTLY:
            {{"answer": {{"state": "Bihar"}}, "log_url": "{log_url}"}}

            FORMATTING RULES:
            - Wrap the main result payload inside the top-level "answer" key.
            - Output ONLY raw valid JSON text.
            - NEVER wrap in markdown code blocks (do NOT use ```json or ```).
            - Absolute zero extra prose, greetings, explanations, or notes outside the JSON object.
            - Utilize conversation history for context if the user asks a follow-up question.
            """
        
        # Build full prompt using system prompt + clean pruned history
        full_messages = [{"role": "system", "content": system_prompt}] + CONVERSATION_HISTORY[chat_id]["messages"]
        
        try:
            response = await client.chat.completions.create(
                model="gpt-5.6-sol",
                messages=full_messages
            )
            
            raw_reply = response.choices[0].message.content.strip()
            
            # Clean markdown formatting if present
            if raw_reply.startswith("```json"):
                raw_reply = raw_reply[7:-3].strip()
            elif raw_reply.startswith("```"):
                raw_reply = raw_reply[3:-3].strip()
                
            # Append assistant response to history
            CONVERSATION_HISTORY[chat_id]["messages"].append({"role": "assistant", "content": raw_reply})

            # Parse JSON safely for logging
            try:
                parsed_json = json.loads(raw_reply)
            except json.JSONDecodeError:
                parsed_json = {"raw_response": raw_reply}

            log_interaction(chat_id, user_message, parsed_json)
            await send_telegram_message(chat_id, raw_reply)
            
        except Exception as e:
             print(f"Error processing request: {e}")
             
    return {"status": "ok"}

@app.get("/run.jsonl")
async def serve_log():
    """Serves the run.jsonl log file for wget requests."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            pass
    return FileResponse(LOG_FILE, media_type="application/jsonlines")