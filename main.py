import asyncio
import argparse
import sys
import uvicorn
import os
import base64
from core.session_manager import BrowserlessSessionManager
from core.chat_handler import DeepSeekChatHandler
from core import config
from core.database import get_db
from core.models import Session as SessionModel, Chat as ChatModel, Message as MessageModel
from core.data_manager import DataManager
from sqlalchemy.future import select
from sqlalchemy import or_
import uuid
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field
from typing import Optional, List
from datetime import datetime
from contextlib import asynccontextmanager

# --- API Models & State ---
class ChatRequest(BaseModel):
    message: str = Field(..., json_schema_extra={"example": "Halo, siapa namamu?"})
    chat_id: Optional[str] = Field(None, json_schema_extra={"example": "02018e6f-cf61-44c0-9479-726759cd4f6f"})
    image_path: Optional[str] = Field(None, json_schema_extra={"example": "/path/to/image.jpg"})
    image_base64: Optional[str] = Field(None, json_schema_extra={"description": "Base64 encoded image string"})

class ChatResponse(BaseModel):
    status: str = Field(..., json_schema_extra={"example": "success"})
    chat_id: str = Field(..., json_schema_extra={"example": "02018e6f-cf61-44c0-9479-726759cd4f6f"})
    response: str = Field(..., json_schema_extra={"example": "Halo! Saya adalah AI..."})

class ChatListItem(BaseModel):
    id: str = Field(..., json_schema_extra={"example": "02018e6f-cf61-44c0-9479-726759cd4f6f"})
    chat_id: Optional[str] = Field(None, json_schema_extra={"example": "12345678"})
    title: Optional[str] = Field(None, json_schema_extra={"example": "Judul Chat Baru"})
    created_at: datetime = Field(..., json_schema_extra={"example": "2023-10-27T10:00:00"})

class APIState:
    session_manager: Optional[BrowserlessSessionManager] = None
    handler_pool: asyncio.Queue = asyncio.Queue()
    _handlers: List[DeepSeekChatHandler] = []

api_state = APIState()

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Setup for API mode
    max_workers = config.CONFIG.get("max_concurrency", 5)
    print(f"🚀 Initializing {max_workers} browser pages for API pool...")
    
    app_manager, handlers = await initialize_deepseek(num_pages=max_workers)
    if app_manager and handlers:
        api_state.session_manager = app_manager
        api_state._handlers = handlers
        for h in handlers:
            api_state.handler_pool.put_nowait(h)
        print(f"✅ API ready with {len(handlers)} workers.")
    else:
        print("❌ API failed to initialize browser pool.")
    
    yield
    
    # Teardown
    if api_state.session_manager:
        await cleanup_deepseek(api_state.session_manager)

api_app = FastAPI(title="DeepSeek Scrapper API", lifespan=lifespan)

async def initialize_deepseek(num_pages: int = 1):
    # Gunakan session ID yang spesifik untuk akun agar cookies tidak bercampur
    user_email = config.CONFIG.get("user", "default")
    safe_email = user_email.replace("@", "_").replace(".", "_")
    session_id = f"deepseek-{safe_email}"

    app = BrowserlessSessionManager(
        browserless_url=config.CONFIG["browserless_url"],
        site_name="deepseek",
        session_dir="deepseek_sessions",
        session_id=session_id
    )
    
    # Ambil session dari DB jika ada
    storage_state = None
    async for db in get_db():
        dm = DataManager(db)
        storage_state = await dm.get_browser_session(app.session_id)

    connected = await app.connect_browserless(storage_state=storage_state)
    if not connected:
        print("Exiting because Browserless connection failed.")
        return None, []

    # Login check dengan satu halaman utama dulu
    main_page = await app.new_page()
    if not main_page:
        print("Exiting because main page could not be created.")
        await app.close(save_before_close=False)
        return None, []
        
    url = "https://chat.deepseek.com"
    await main_page.goto(url, wait_until="domcontentloaded")
    
    login_indicators = [
        "textarea[placeholder*='Message DeepSeek']",
        "div[class*='ede5bc47']",
        "div[class*='_9d8da05']",
        "textarea#chat-input",
        ".ds-avatar"
    ]
    
    is_logged_in = False
    for indicator in login_indicators:
        try:
            await main_page.wait_for_selector(indicator, state="visible", timeout=2000)
            is_logged_in = True
            break
        except:
            continue

    if not is_logged_in:
        print("🔐 Session expired atau belum login, melakukan login otomatis...")
        try:
            login_success = asyncio.Future()
            async def handle_response(response):
                if "/api/v0/users/login" in response.url and response.status == 200:
                    if not login_success.done():
                        login_success.set_result(True)
            main_page.on("response", handle_response)

            if "login" not in main_page.url.lower():
                try:
                    login_btn = await main_page.query_selector('text="Log in"')
                    if login_btn:
                        await login_btn.click()
                        await main_page.wait_for_load_state("networkidle")
                except:
                    pass

            await main_page.wait_for_selector("input[type='text'].ds-input__input", timeout=10000)
            await main_page.fill("input[type='text'].ds-input__input", config.CONFIG["user"])
            await main_page.fill("input[type='password'].ds-input__input", config.CONFIG["password"])
            await main_page.click("div.ds-sign-up-form__register-button[role='button']")

            try:
                await asyncio.wait_for(login_success, timeout=10000)
                print("🌐 Network: Login success")
            except:
                await main_page.wait_for_selector("textarea[placeholder*='Message DeepSeek']", timeout=10000)
            
            print("✅ Login otomatis berhasil.")
            main_page.remove_listener("response", handle_response)
        except Exception as e:
            print(f"❌ Login otomatis gagal: {e}")
            if "chat" in main_page.url:
                print("⚠️ Lanjut meskipun deteksi error.")
            else:
                await app.close(save_before_close=False)
                return None, []

    # Buat pool halaman
    handlers = [DeepSeekChatHandler(main_page)]
    if num_pages > 1:
        print(f"📑 Opening {num_pages-1} additional pages...")
        for i in range(num_pages - 1):
            page = await app.new_page()
            await page.goto(url, wait_until="domcontentloaded")
            handlers.append(DeepSeekChatHandler(page))
            print(f"  - Page {i+2} opened.")

    return app, handlers

async def cleanup_deepseek(app: BrowserlessSessionManager):
    print("\nMenyimpan session dan menutup browser...")
    try:
        new_storage_state = await app.get_storage_state()
        if new_storage_state:
            async for db in get_db():
                dm = DataManager(db)
                await dm.save_browser_session(
                    app.session_id, 
                    config.CONFIG["user"], 
                    new_storage_state
                )
        await app.close(save_before_close=False)
    except Exception as e:
        print(f"⚠️ Error saat menutup browser: {e}")

@api_app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(request: ChatRequest):
    # Acquire handler from pool
    handler = await api_state.handler_pool.get()
    try:
        # Logic chat_id "new" untuk membuat chat baru
        if request.chat_id == "new":
            chat_uuid = str(uuid.uuid4())
            print("🆕 Request chat baru terdeteksi, mengarahkan ke halaman utama...")
            await handler.page.goto("https://chat.deepseek.com/", wait_until="networkidle")
        else:
            chat_uuid = request.chat_id or str(uuid.uuid4())
        
        session_id = api_state.session_manager.session_id
        
        # Handle image_base64
        temp_image_path = None
        if request.image_base64:
            try:
                # Create temp directory if not exists
                os.makedirs("temp_uploads", exist_ok=True)
                temp_image_path = f"temp_uploads/{uuid.uuid4()}.png"
                
                # Decode base64
                header, data = request.image_base64.split(",") if "," in request.image_base64 else (None, request.image_base64)
                with open(temp_image_path, "wb") as f:
                    f.write(base64.b64decode(data))
            except Exception as e:
                raise HTTPException(status_code=400, detail=f"Invalid base64 image data: {e}")

        # Use image_path if provided, otherwise use temp_image_path from base64
        final_image_path = request.image_path or temp_image_path

        user_email = config.CONFIG.get("user")
        async for db in get_db():
            dm = DataManager(db)
            
            # Jika chat_id diberikan, pastikan browser di URL yang benar
            if request.chat_id and request.chat_id != "new":
                # Cari di DB untuk mendapatkan real_chat_id (DeepSeek ID)
                stmt = select(ChatModel).where(or_(ChatModel.chat_id == request.chat_id, ChatModel.id == request.chat_id))
                res = await db.execute(stmt)
                db_chat = res.scalar_one_or_none()
                
                if db_chat and db_chat.chat_id and len(db_chat.chat_id) < 40: # Jika bukan UUID temp
                    chat_url = f"https://chat.deepseek.com/a/chat/s/{db_chat.chat_id}"
                    if db_chat.chat_id not in handler.page.url:
                        print(f"🔗 Mengarahkan API ke link chat: {chat_url}")
                        await handler.page.goto(chat_url, wait_until="networkidle")

            await dm.save_chat_message(session_id, chat_uuid, "user", request.message, account_email=user_email)
            
            try:
                success = await handler.send_message(request.message, image_path=final_image_path)
                if success:
                    current_url = handler.page.url
                    if "/a/chat/s/" in current_url:
                        real_chat_id = current_url.split("/a/chat/s/")[-1].split("?")[0].split("#")[0]
                        await dm.update_chat_id(chat_uuid, real_chat_id)
                        chat_uuid = real_chat_id

                    await handler.wait_for_response()
                    response_text = await handler.get_latest_response()
                    
                    # Coba ambil title chat baru jika belum ada judul
                    chat_title = await handler.get_chat_title()
                    if chat_title:
                        await dm.update_chat_title(chat_uuid, chat_title)
                    
                    if response_text:
                        await dm.save_chat_message(session_id, chat_uuid, "assistant", response_text, account_email=user_email)
                        return {"status": "success", "chat_id": chat_uuid, "response": response_text}
                    
                    raise HTTPException(status_code=500, detail="Failed to get AI response")
                
                raise HTTPException(status_code=500, detail="Failed to send message")
            finally:
                # Cleanup temp file
                if temp_image_path and os.path.exists(temp_image_path):
                    os.remove(temp_image_path)
    finally:
        # Release handler back to pool
        api_state.handler_pool.put_nowait(handler)

@api_app.get("/chats", response_model=List[ChatListItem])
async def list_chats_endpoint():
    user_email = config.CONFIG.get("user")
    async for db in get_db():
        dm = DataManager(db)
        # Ambil chat berdasarkan email akun agar lebih stabil saat rotasi session
        chats = await dm.get_chats(account_email=user_email)
        return [
            {
                "id": chat.id,
                "chat_id": chat.chat_id,
                "title": chat.title,
                "created_at": chat.created_at
            }
            for chat in chats
        ]

async def run_chat_mode(chat_handler: DeepSeekChatHandler, session_id: str):
    print("\n💬 Mode Chat Aktif. Ketik 'exit' untuk keluar.")
    
    async for db in get_db():
        dm = DataManager(db)
        
        # 1. Sync Akun
        await dm.sync_account(config.CONFIG["user"], config.CONFIG["password"])
        
        # 2. Ambil Chat Terakhir dari DB
        stmt = select(ChatModel).where(ChatModel.session_id == session_id).order_by(ChatModel.created_at.desc()).limit(1)
        result = await db.execute(stmt)
        last_chat = result.scalar_one_or_none()
        
        chat_uuid = None
        
        if last_chat and last_chat.chat_id:
            last_chat_url = f"https://chat.deepseek.com/a/chat/s/{last_chat.chat_id}"
            print(f"🔄 Mencoba melanjutkan chat terakhir: {last_chat_url}")
            
            # Berpindah ke URL chat terakhir
            await chat_handler.page.goto(last_chat_url, wait_until="networkidle")
            
            # Verifikasi apakah link benar-benar terbuka dan bukan redirect ke /
            current_url = chat_handler.page.url
            if last_chat.chat_id and last_chat.chat_id in current_url:
                print(f"✅ Berhasil memuat chat terakhir: {last_chat.chat_id}")
                chat_uuid = last_chat.chat_id
                # Tunggu input box muncul
                try:
                    await chat_handler.page.wait_for_selector("textarea", timeout=5000)
                except:
                    pass
            else:
                print(f"⚠️  Link chat terakhir tidak valid (URL: {current_url}), menghapus dari DB.")
                await dm.delete_chat(last_chat.chat_id)
                await chat_handler.page.goto("https://chat.deepseek.com", wait_until="networkidle")
                chat_uuid = str(uuid.uuid4())
        else:
            if not last_chat:
                print("🆕 Memulai chat baru (tidak ada history).")
            else:
                print("🆕 Memulai chat baru (chat terakhir tidak memiliki ID valid).")
            await chat_handler.page.goto("https://chat.deepseek.com", wait_until="networkidle")
            chat_uuid = str(uuid.uuid4())

        while True:
            user_message = await asyncio.get_event_loop().run_in_executor(
                None, lambda: input("\n👤 Anda: ").strip()
            )
            
            if user_message.lower() in ['exit', 'quit', 'keluar']:
                break
            if not user_message:
                continue

            # 3. Pastikan URL browser sinkron dengan chat_uuid
            is_new_chat = len(chat_uuid) > 30 
            if not is_new_chat:
                expected_url = f"https://chat.deepseek.com/a/chat/s/{chat_uuid}"
                if chat_uuid not in chat_handler.page.url:
                    print(f"🔗 Mengarahkan kembali ke link chat: {expected_url}")
                    await chat_handler.page.goto(expected_url, wait_until="networkidle")

            # 4. Simpan & Kirim Pesan
            user_email = config.CONFIG.get("user")
            await dm.save_chat_message(session_id, chat_uuid, "user", user_message, account_email=user_email)
            success = await chat_handler.send_message(user_message)
            
            if success:
                # 3. Update Chat ID jika DeepSeek melakukan redirect (hanya jika saat ini chat baru/UUID)
                current_url = chat_handler.page.url
                if "/a/chat/s/" in current_url:
                    # Ekstraksi chat_id yang lebih presisi dari URL
                    # Format: https://chat.deepseek.com/a/chat/s/ID-DISINI
                    real_chat_id = current_url.split("/a/chat/s/")[-1].split("?")[0].split("#")[0]
                    
                    # HANYA update jika kita sedang menggunakan temporary UUID
                    if is_new_chat and real_chat_id != chat_uuid:
                        print(f"🆔 Mendeteksi chat ID baru dari URL: {real_chat_id}")
                        await dm.update_chat_id(chat_uuid, real_chat_id)
                        chat_uuid = real_chat_id
                    elif not is_new_chat and real_chat_id != chat_uuid:
                        # Jika kita sedang di chat lama tapi URL berubah (misal DeepSeek ganti ID)
                        print(f"📍 URL berubah, mengikuti ID baru: {real_chat_id}")
                        chat_uuid = real_chat_id

                await chat_handler.wait_for_response()
                response_text = await chat_handler.get_latest_response()
                
                # Update title jika ada
                chat_title = await chat_handler.get_chat_title()
                if chat_title:
                    await dm.update_chat_title(chat_uuid, chat_title)
                
                if response_text:
                    print(f"\n🤖 AI:\n{'-'*30}\n{response_text}\n{'-'*30}")
                    # 4. Simpan pesan AI ke DB
                    await dm.save_chat_message(session_id, chat_uuid, "assistant", response_text, account_email=user_email)
                else:
                    print("⚠️ Gagal mengambil teks respon.")
            else:
                print("❌ Gagal mengirim pesan.")

async def main():
    parser = argparse.ArgumentParser(description="DeepSeek Scrapper CLI & API")
    parser.add_argument("--mode", choices=["chat", "api"], default="chat", help="Mode operasi (chat atau api)")
    parser.add_argument("--port", type=int, default=8000, help="Port untuk mode API")
    args = parser.parse_args()

    if args.mode == "chat":
        app, chat_handler = await initialize_deepseek()
        if not app or not chat_handler:
            return
            
        try:
            await run_chat_mode(chat_handler, app.session_id)
        finally:
            await cleanup_deepseek(app)
            
    elif args.mode == "api":
        # Lifespan will handle initialization when uvicorn starts
        print(f"🚀 Starting API server on port {args.port}...")
        config_uvicorn = uvicorn.Config(
            api_app, 
            host="0.0.0.0", 
            port=args.port, 
            log_level="info",
            loop="asyncio"
        )
        server = uvicorn.Server(config_uvicorn)
        await server.serve()

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Keluar atas permintaan user...")
    except Exception as e:
        print(f"❌ Error fatal: {e}")
