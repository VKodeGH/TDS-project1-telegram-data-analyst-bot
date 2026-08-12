import os
import json
import time
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
LOG_FILE = "run.jsonl"

# Memory settings
MAX_HISTORY_MESSAGES = 10    # Keeps last 3 turns (3 user + 3 assistant)
INACTIVITY_TIMEOUT = 360    # Clear memory after 2 minutes of silence

# Stores chat memory and last message timestamp per chat_id:
# { chat_id: {"messages": [...], "last_seen": float} }
CONVERSATION_HISTORY = {}

client = AsyncOpenAI(
    api_key=AIPIPE_TOKEN,
    base_url="https://aipipe.org/openai/v1"
)

async def send_telegram_message(chat_id, text):
    """Sends the raw JSON response back to Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, json=payload)

def log_interaction(chat_id, query, response_data):
    """Saves the interaction to the local JSONL log file."""
    log_entry = {"chat_id": chat_id, "query": query, "response": response_data}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receives incoming messages from Telegram."""
    update = await request.json()
    
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