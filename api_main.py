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
    # Startup: Initialize browser connection
    state.session_manager = BrowserlessSessionManager(
        browserless_url=config.CONFIG["browserless_url"],
        site_name="deepseek",
        session_id="deepseek-persistent-session" # Harus sama dengan main.py
    )
    
    connected = await state.session_manager.connect_browserless()
    if not connected:
        print("❌ Failed to connect to Browserless on startup")
    else:
        page = await state.session_manager.new_page()
        url = "https://chat.deepseek.com"
        
        # Load session jika ada storage state (sinkronisasi lokal)
        if state.session_manager.storage_file.exists():
            await state.session_manager.load_session()
            
        await page.goto(url, wait_until="domcontentloaded")
        
        # Cek apakah user sudah login (Logika yang sama dengan main.py)
        # Gunakan XPath textarea sebagai indikator login
        login_indicator = "xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[1]/div[2]/div[2]/div[1]/div[1]/div[1]/textarea[1]"
        
        try:
            await page.wait_for_selector(login_indicator, state="visible", timeout=10000)
            print("✅ Session API sudah aktif (Logged in).")
        except:
            print("🔐 API Session expired/not found, melakukan login otomatis...")
            try:
                # Login logic dari main.py
                await page.type("input.ds-input__input", config.CONFIG["user"])
                await page.type("input[type='password']", config.CONFIG["password"])  
                await page.click('"Log in"')
                
                # Tunggu login sukses
                await page.wait_for_selector(login_indicator, state="visible", timeout=15000)
                print("✅ Login otomatis API berhasil.")
                await state.session_manager.save_session()
            except Exception as e:
                print(f"❌ Login otomatis API gagal: {e}")

        state.chat_handler = DeepSeekChatHandler(page)
        print("🚀 API ready and browser connected with persistent session")
        
    yield
    
    # Shutdown: Clean up
    if state.session_manager:
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

@app.post("/chat", response_model=ChatResponse, tags=["Chat"])
async def chat_endpoint(request: ChatRequest):
    """
    Mengirim pesan ke DeepSeek dan mengambil responnya.
    """
    if state.is_busy:
        raise HTTPException(status_code=503, detail="AI sedang sibuk memproses permintaan lain")
    
    if not state.chat_handler:
        raise HTTPException(status_code=500, detail="Browser tidak terhubung")

    temp_file_path = None
    try:
        state.is_busy = True
        
        # Handle Base64 Image
        final_image_path = request.image_path
        if request.image_base64:
            try:
                # Decode base64
                image_data = base64.b64decode(request.image_base64)
                # Buat temporary file
                filename = f"temp_upload_{uuid.uuid4()}.jpg"
                temp_file_path = os.path.join("/tmp", filename)
                
                with open(temp_file_path, "wb") as f:
                    f.write(image_data)
                
                final_image_path = temp_file_path
                print(f"🖼️ Base64 image decoded to {temp_file_path}")
            except Exception as e:
                print(f"❌ Failed to process base64 image: {e}")
                state.is_busy = False
                return ChatResponse(status="error", error=f"Invalid base64 image: {str(e)}")

        # 1. Navigasi ke chat lama jika ada chat_id (fitur baru yang didukung)
        # Catatan: Perlu dipastikan ChatRequest memiliki field chat_id
        chat_id = getattr(request, 'chat_id', None)
        if chat_id:
            target_url = f"https://chat.deepseek.com/a/chat/s/{chat_id}"
            if state.chat_handler.page.url != target_url:
                await state.chat_handler.page.goto(target_url, wait_until="domcontentloaded")
                await asyncio.sleep(2) # Tunggu loading chat history
        
        # 2. Kirim pesan (dengan opsional image_path)
        success = await state.chat_handler.send_message(request.message, image_path=final_image_path)
        if not success:
            state.is_busy = False
            return ChatResponse(status="error", error="Gagal mengirim pesan ke UI")

        # 3. Tunggu dan ambil respon
        await state.chat_handler.wait_for_response()
        ai_response = await state.chat_handler.get_latest_response()
        
        # 4. Ambil chat_id saat ini dari URL (untuk dikembalikan ke user)
        current_url = state.chat_handler.page.url
        new_chat_id = current_url.split('/')[-1] if '/a/chat/s/' in current_url else None
        
        state.is_busy = False
        return ChatResponse(status="success", response=ai_response, chat_id=new_chat_id)

    except Exception as e:
        state.is_busy = False
        return ChatResponse(status="error", error=str(e))
    finally:
        # Cleanup temp file
        if temp_file_path and os.path.exists(temp_file_path):
            try:
                os.remove(temp_file_path)
                print(f"Clarified: 🧹 Removed temp file: {temp_file_path}")
            except Exception as cleanup_err:
                print(f"⚠️ Failed to remove temp file: {cleanup_err}")

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
