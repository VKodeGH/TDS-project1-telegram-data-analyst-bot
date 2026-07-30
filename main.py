import os
import json
import httpx
from fastapi import FastAPI, Request
from fastapi.responses import FileResponse
from openai import AsyncOpenAI
from dotenv import load_dotenv

# Load hidden keys from .env (for local WSL testing)
load_dotenv()

BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN")
AIPIPE_TOKEN = os.getenv("AIPIPE_TOKEN")
HOST_URL = os.getenv("HOST_URL", "http://localhost:8000") 

app = FastAPI()
LOG_FILE = "run.jsonl"

# Configure OpenAI to use your college's AI Pipe
client = AsyncOpenAI(
    api_key=AIPIPE_TOKEN,
    base_url="https://aipipe.org/openai/v1"
)

async def send_telegram_message(chat_id, text):
    """Sends the JSON response back to Telegram."""
    url = f"https://api.telegram.org/bot{BOT_TOKEN}/sendMessage"
    payload = {"chat_id": chat_id, "text": text}
    async with httpx.AsyncClient() as http_client:
        await http_client.post(url, json=payload)

def log_interaction(chat_id, query, response_text):
    """Saves the interaction to the local log file."""
    log_entry = {"chat_id": chat_id, "query": query, "response": response_text}
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(json.dumps(log_entry) + "\n")

@app.post("/webhook")
async def telegram_webhook(request: Request):
    """Receives messages from Telegram."""
    update = await request.json()
    
    if "message" in update and "text" in update["message"]:
        chat_id = update["message"]["chat"]["id"]
        user_message = update["message"]["text"]
        
        # The URL where the grader downloads your log
        log_url = f"{HOST_URL}/run.jsonl"
        
        # Strict instructions for the LLM
        system_prompt = f"""
        You are a data analysis bot. The user will ask a question and specify a JSON format.
        You must output ONLY valid JSON.
        The final JSON MUST include the key 'log_url' with the value: '{log_url}'.
        Do not include markdown blocks (like ```json), greetings, or any other text.
        """
        
        try:
            # Call AI Pipe (OpenRouter)
            response = await client.chat.completions.create(
                model="gpt-4o-mini",
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_message}
                ],
                temperature=0.0
            )
            
            raw_reply = response.choices[0].message.content.strip()
            
            # Clean markdown if the LLM adds it by mistake
            if raw_reply.startswith("```json"):
                raw_reply = raw_reply[7:-3].strip()
            elif raw_reply.startswith("```"):
                raw_reply = raw_reply[3:-3].strip()
                
            # Log it and send it
            log_interaction(chat_id, user_message, json.loads(raw_reply))
            await send_telegram_message(chat_id, raw_reply)
            
        except Exception as e:
             print(f"Error: {e}")
             
    return {"status": "ok"}

@app.get("/run.jsonl")
async def serve_log():
    """Serves the JSONL log file for the grader."""
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w") as f:
            pass
    return FileResponse(LOG_FILE, media_type="application/jsonlines")
