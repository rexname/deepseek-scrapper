import asyncio
import os
import base64
import uuid
from fastapi import FastAPI, HTTPException, BackgroundTasks
from pydantic import BaseModel, Field
from typing import Optional, List
from core.session_manager import BrowserlessSessionManager
from core.chat_handler import DeepSeekChatHandler
from core import config
from core.database import get_db
from core.models import Session as SessionModel, Chat as ChatModel, Message as MessageModel
from core.data_manager import DataManager
from sqlalchemy.future import select
from contextlib import asynccontextmanager

# --- Models ---
class ChatRequest(BaseModel):
    message: str = Field(..., example="Halo, siapa namamu?")
    session_id: str = Field("default-session", example="user-123")
    image_path: Optional[str] = Field(None, example="/path/to/image.jpg")
    image_base64: Optional[str] = Field(None, description="Base64 encoded image string")

class ChatResponse(BaseModel):
    status: str
    response: Optional[str] = None
    error: Optional[str] = None

# --- Global State ---
# Di tahap awal ini, kita gunakan satu instance global untuk demo
# Kedepannya bisa dikembangkan untuk multi-session
class GlobalState:
    session_manager: Optional[BrowserlessSessionManager] = None
    chat_handler: Optional[DeepSeekChatHandler] = None
    is_busy: bool = False

state = GlobalState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Jika diluncurkan dari main.py, session_manager dan chat_handler sudah di-inject
    # Jika dijalankan langsung (standalone), baru lakukan inisialisasi
    if not state.session_manager:
        print("🔧 Initializing standalone API session...")
        state.session_manager = BrowserlessSessionManager(
            browserless_url=config.CONFIG["browserless_url"],
            site_name="deepseek",
            session_id="deepseek-persistent-session"
        )
        
        connected = await state.session_manager.connect_browserless()
        if not connected:
            print("❌ Failed to connect to Browserless on startup")
        else:
            page = await state.session_manager.new_page()
            url = "https://chat.deepseek.com"
            
            if state.session_manager.storage_file.exists():
                await state.session_manager.load_session()
                
            await page.goto(url, wait_until="domcontentloaded")
            
            login_indicator = "xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[1]/div[2]/div[2]/div[1]/div[1]/div[1]/textarea[1]"
            
            try:
                await page.wait_for_selector(login_indicator, state="visible", timeout=10000)
                print("✅ Session API sudah aktif.")
            except:
                print("🔐 API Session expired, melakukan login otomatis...")
                try:
                    await page.type("input.ds-input__input", config.CONFIG["user"])
                    await page.type("input[type='password']", config.CONFIG["password"])  
                    await page.click('"Log in"')
                    await page.wait_for_selector(login_indicator, state="visible", timeout=15000)
                    print("✅ Login otomatis API berhasil.")
                    await state.session_manager.save_session()
                except Exception as e:
                    print(f"❌ Login otomatis API gagal: {e}")

            state.chat_handler = DeepSeekChatHandler(page)
            print("🚀 API ready (standalone mode)")
    else:
        print("🚀 API ready (injected mode from main.py)")
        
    yield
    
    # Shutdown: Clean up hanya jika standalone
    # Jika dari main.py, biar main.py yang handle closing
    if state.session_manager and not sys.modules.get('main'):
        await state.session_manager.close()

app = FastAPI(
    title="DeepSeek Scrapper API",
    description="API untuk berinteraksi dengan DeepSeek Chat secara otomatis",
    version="1.0.0",
    lifespan=lifespan
)

@app.get("/", tags=["General"])
async def root():
    return {"message": "DeepSeek Scrapper API is running", "docs": "/docs"}

@app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    if not state.chat_handler:
        raise HTTPException(status_code=503, detail="Chat handler not initialized")
    
    chat_uuid = str(uuid.uuid4())
    session_id = state.session_manager.session_id
    
    async for db in get_db():
        dm = DataManager(db)
        
        # 1. Simpan pesan User
        await dm.save_chat_message(session_id, chat_uuid, "user", request.message)
        
        # 2. Kirim pesan ke Browser
        success = await state.chat_handler.send_message(request.message, image_path=request.image_path)
        
        if success:
            # 3. Sinkronisasi Chat ID dari URL
            current_url = state.chat_handler.page.url
            if "/a/chat/s/" in current_url:
                real_chat_id = current_url.split("/")[-1]
                await dm.update_chat_id(chat_uuid, real_chat_id)
                chat_uuid = real_chat_id

            await state.chat_handler.wait_for_response()
            response_text = await state.chat_handler.get_latest_response()
            
            if response_text:
                # 4. Simpan respon AI
                await dm.save_chat_message(session_id, chat_uuid, "assistant", response_text)
                return {"status": "success", "chat_id": chat_uuid, "response": response_text}
            
            raise HTTPException(status_code=500, detail="Failed to get AI response")
            
        raise HTTPException(status_code=500, detail="Failed to send message")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
