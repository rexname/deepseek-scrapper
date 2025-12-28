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
from typing import Optional

# --- API Models & State ---
class ChatRequest(BaseModel):
    message: str = Field(..., json_schema_extra={"example": "Halo, siapa namamu?"})
    chat_id: Optional[str] = Field(None, json_schema_extra={"example": "02018e6f-cf61-44c0-9479-726759cd4f6f"})
    image_path: Optional[str] = Field(None, json_schema_extra={"example": "/path/to/image.jpg"})
    image_base64: Optional[str] = Field(None, json_schema_extra={"description": "Base64 encoded image string"})

class APIState:
    session_manager: Optional[BrowserlessSessionManager] = None
    chat_handler: Optional[DeepSeekChatHandler] = None

api_state = APIState()
api_app = FastAPI(title="DeepSeek Scrapper API")

@api_app.post("/chat")
async def chat_endpoint(request: ChatRequest):
    if not api_state.chat_handler:
        raise HTTPException(status_code=503, detail="Chat handler not initialized")
    
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

    async for db in get_db():
        dm = DataManager(db)
        
        # Jika chat_id diberikan, pastikan browser di URL yang benar
        if request.chat_id:
            # Cari di DB untuk mendapatkan real_chat_id (DeepSeek ID)
            stmt = select(ChatModel).where(or_(ChatModel.chat_id == request.chat_id, ChatModel.id == request.chat_id))
            res = await db.execute(stmt)
            db_chat = res.scalar_one_or_none()
            
            if db_chat and db_chat.chat_id and len(db_chat.chat_id) < 40: # Jika bukan UUID temp
                chat_url = f"https://chat.deepseek.com/a/chat/s/{db_chat.chat_id}"
                if db_chat.chat_id not in api_state.chat_handler.page.url:
                    print(f"🔗 Mengarahkan API ke link chat: {chat_url}")
                    await api_state.chat_handler.page.goto(chat_url, wait_until="networkidle")

        await dm.save_chat_message(session_id, chat_uuid, "user", request.message)
        
        try:
            success = await api_state.chat_handler.send_message(request.message, image_path=final_image_path)
            if success:
                current_url = api_state.chat_handler.page.url
                if "/a/chat/s/" in current_url:
                    real_chat_id = current_url.split("/a/chat/s/")[-1].split("?")[0].split("#")[0]
                    await dm.update_chat_id(chat_uuid, real_chat_id)
                    chat_uuid = real_chat_id

                await api_state.chat_handler.wait_for_response()
                response_text = await api_state.chat_handler.get_latest_response()
                
                if response_text:
                    await dm.save_chat_message(session_id, chat_uuid, "assistant", response_text)
                    return {"status": "success", "chat_id": chat_uuid, "response": response_text}
                
                raise HTTPException(status_code=500, detail="Failed to get AI response")
            
            raise HTTPException(status_code=500, detail="Failed to send message")
        finally:
            # Cleanup temp file
            if temp_image_path and os.path.exists(temp_image_path):
                os.remove(temp_image_path)

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
            await dm.save_chat_message(session_id, chat_uuid, "user", user_message)
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
                
                if response_text:
                    print(f"\n🤖 AI:\n{'-'*30}\n{response_text}\n{'-'*30}")
                    # 4. Simpan pesan AI ke DB
                    await dm.save_chat_message(session_id, chat_uuid, "assistant", response_text)
                else:
                    print("⚠️ Gagal mengambil teks respon.")
            else:
                print("❌ Gagal mengirim pesan.")

async def main():
    parser = argparse.ArgumentParser(description="DeepSeek Scrapper CLI & API")
    parser.add_argument("--mode", choices=["chat", "api"], default="chat", help="Mode operasi (chat atau api)")
    parser.add_argument("--port", type=int, default=8000, help="Port untuk mode API")
    args = parser.parse_args()

    app = BrowserlessSessionManager(
        browserless_url=config.CONFIG["browserless_url"],
        site_name="deepseek",
        session_dir="deepseek_sessions",
        session_id="deepseek-persistent-session"
    )
    
    # Ambil session dari DB jika ada
    storage_state = None
    async for db in get_db():
        dm = DataManager(db)
        storage_state = await dm.get_browser_session(app.session_id)

    connected = await app.connect_browserless(storage_state=storage_state)
    if not connected:
        print("Exiting because Browserless connection failed.")
        return

    page = await app.new_page()
    if not page:
        print("Exiting because page could not be created.")
        await app.close(save_before_close=False)
        return
        
    url = "https://chat.deepseek.com"
    await page.goto(url, wait_until="domcontentloaded")
    
    # Login check dengan deteksi network dan DOM terbaru
    login_indicators = [
        "textarea[placeholder*='Message DeepSeek']",
        "div[class*='ede5bc47']",  # Avatar lingkaran (M)
        "div[class*='_9d8da05']",  # Email display
        "textarea#chat-input",
        ".ds-avatar"
    ]
    
    is_logged_in = False
    
    # 1. Cek apakah sudah login berdasarkan elemen yang ada
    for indicator in login_indicators:
        try:
            await page.wait_for_selector(indicator, state="visible", timeout=1000)
            is_logged_in = True
            break
        except:
            continue

    if is_logged_in:
        print("✅ Session sudah aktif.")
    else:
        print("🔐 Session expired atau belum login, melakukan login otomatis...")
        try:
            # Pantau network untuk konfirmasi login sukses (200 OK pada endpoint login)
            login_success = asyncio.Future()
            
            async def handle_response(response):
                if "/api/v0/users/login" in response.url and response.status == 200:
                    if not login_success.done():
                        login_success.set_result(True)
            
            page.on("response", handle_response)

            # Jika tidak di halaman login, cari tombol login
            if "login" not in page.url.lower():
                try:
                    login_btn = await page.query_selector('text="Log in"')
                    if login_btn:
                        await login_btn.click()
                        await page.wait_for_load_state("networkidle")
                except:
                    pass

            # Isi form login sesuai HTML yang diberikan user
            # Username/Email
            email_input = "input[type='text'].ds-input__input[placeholder*='Phone']"
            await page.wait_for_selector(email_input, timeout=10000)
            await page.fill(email_input, config.CONFIG["user"])
            
            # Password
            pass_input = "input[type='password'].ds-input__input[placeholder='Password']"
            await page.fill(pass_input, config.CONFIG["password"])
            
            # Submit Button (berdasarkan HTML div role=button yang diberikan)
            submit_btn = "div.ds-sign-up-form__register-button[role='button']"
            await page.click(submit_btn)

            # Tunggu konfirmasi dari Network atau elemen dashboard
            try:
                # Prioritas 1: Tunggu respons network 200 OK
                await asyncio.wait_for(login_success, timeout=1000)
                print("🌐 Network: Login success (200 OK)")
            except:
                # Prioritas 2: Tunggu elemen textarea dashboard muncul
                await page.wait_for_selector("textarea[placeholder*='Message DeepSeek']", timeout=10000)
            
            print("✅ Login otomatis berhasil.")
            
            # Bersihkan listener
            page.remove_listener("response", handle_response)
            
        except Exception as e:
            print(f"❌ Login otomatis gagal: {e}")
            if "chat" in page.url:
                print("⚠️  URL menunjukkan area chat, lanjut meskipun deteksi error.")
            else:
                await app.close(save_before_close=False)
                return

    chat_handler = DeepSeekChatHandler(page)

    if args.mode == "chat":
        await run_chat_mode(chat_handler, app.session_id)
    elif args.mode == "api":
        # Inject state untuk API
        api_state.session_manager = app
        api_state.chat_handler = chat_handler
        
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

    print("\nMenyimpan session dan menutup browser...")
    try:
        # Simpan session ke DB sebelum tutup
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

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\n👋 Keluar atas permintaan user...")
    except Exception as e:
        print(f"❌ Error fatal: {e}")
