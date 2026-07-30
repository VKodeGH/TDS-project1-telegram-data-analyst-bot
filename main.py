import os
import json
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load hidden keys from .env (for local testing)
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN")
HOST_URL = os.getenv("HOST_URL", "http://localhost:8000") 

app = FastAPI()
LOG_FILE = "run.jsonl"

# Configure OpenAI client using AI Pipe endpoint
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
        
        # Public URL for the grader to download the run log
        log_url = f"{HOST_URL}/run.jsonl"
        
        # Strict System Prompt enforcing the outer {"answer": ..., "log_url": ...} structure
        system_prompt = f"""
        You are a precise data analysis bot.

        CRITICAL OUTPUT CONTRACT:
        Your response MUST be EXACTLY ONE valid raw JSON object with PRECISELY TWO top-level keys:
        1. "answer": The answer payload, shaped EXACTLY as requested in the user's prompt.
        2. "log_url": "{log_url}"

        EXAMPLE FORMAT:
        If the user asks for state name in {{"answer": {{"state": "<state name>"}}}}, your response MUST be:
        {{"answer": {{"state": "Assam"}}, "log_url": "{log_url}"}}

        If the user asks for a list of values, your response MUST be:
        {{"answer": {{"values": [1, 2, 3]}}, "log_url": "{log_url}"}}

        RULES:
        - ALWAYS wrap the result payload inside the top-level "answer" key.
        - Output ONLY raw valid JSON.
        - NEVER wrap in markdown blocks (do NOT use ```json or ```).
        - NO greetings, explanations, or additional prose.
        """
        
        try:
            # Call GPT-5 mini
            response = await client.chat.completions.create(
                model="gpt-5-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ]
            )
            
            raw_reply = response.choices[0].message.content.strip()
            
            # Clean markdown formatting if the model includes it
            if raw_reply.startswith("```json"):
                raw_reply = raw_reply[7:-3].strip()
            elif raw_reply.startswith("```"):
                raw_reply = raw_reply[3:-3].strip()
                
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