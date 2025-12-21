import asyncio
from core.session_manager import BrowserlessSessionManager
from core.chat_handler import DeepSeekChatHandler
from core import config

async def main():
    app = BrowserlessSessionManager(
        browserless_url=config.CONFIG["browserless_url"],
        site_name="deepseek",
        session_dir="deepseek_sessions",
        session_id="deepseek-persistent-session"  # ID session persisten
    )
    connected = await app.connect_browserless()
    if not connected:
        print("Exiting because Browserless connection failed.")
        return

    page = await app.new_page()
    if not page:
        print("Exiting because page could not be created.")
        await app.close(save_before_close=False)
        return
    url = "https://chat.deepseek.com"
    
    # Load session jika ada storage state
    has_session = False
    if app.storage_file.exists():
        print(f"📁 Storage file exists: {app.storage_file}")
        # Note: load_session() sudah otomatis dipanggil di connect_browserless via storage_state param
        # Tapi kita bisa panggil lagi jika ingin eksplisit
        await app.load_session()
        
        # Debug: Check loaded cookies
        cookies = await app.context.cookies()
        print(f"🔍 Active cookies: {len(cookies)}")
        for cookie in cookies:
            if cookie['name'] == 'ds_session_id':
                print(f"   ✅ ds_session_id found")
        
        has_session = True
    
    # Buka halaman dengan session yang sudah dimuat
    await page.goto(url, wait_until="domcontentloaded")
    
    # Cek status session persisten di Browserless (Opsional debug)
    print(f"🔍 Checking persistent session: {app.session_id}")
    
    # Cek apakah user sudah login menggunakan XPath spesifik
    try:
        await page.wait_for_selector("xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[1]/div[2]/div[2]/div[1]/div[1]/div[1]/textarea[1]", state="visible", timeout=10000)
        print("✅ Session sudah aktif, tidak perlu login.")
    except:
        # Jika belum login, lakukan login otomatis
        print("🔐 Session expired, melakukan login otomatis...")
        
        # Debug: Screenshot sebelum login
        try:
            await page.screenshot(path="debug_before_login.png")
            print("📸 Screenshot saved: debug_before_login.png")
        except Exception as screenshot_error:
            print(f"⚠️  Screenshot failed: {screenshot_error}")
        
        # Coba login
        try:
            await page.type("input.ds-input__input", config.CONFIG["user"])
            await page.type("input[type='password']", config.CONFIG["password"])  
            await page.click('"Log in"')
            
            # Tunggu sampai login berhasil dengan XPath spesifik
            await page.wait_for_selector("xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[1]/div[2]/div[2]/div[1]/div[1]/div[1]/textarea[1]", state="visible", timeout=15000)
            print("✅ Login otomatis berhasil.")
            
            # Debug: Screenshot setelah login
            try:
                await page.screenshot(path="debug_after_login.png")
                print("📸 Screenshot saved: debug_after_login.png")
            except Exception as screenshot_error:
                print(f"⚠️  Screenshot failed: {screenshot_error}")
            
        except Exception as e:
            print(f"❌ Login otomatis gagal: {e}")
            # Debug: Screenshot error
            try:
                await page.screenshot(path="debug_login_error.png")
                print("📸 Screenshot saved: debug_login_error.png")
            except Exception as screenshot_error:
                print(f"⚠️  Screenshot failed: {screenshot_error}")
            raise
        
        # Simpan session baru
        await app.save_session()

    # --- CHAT INTERACTION START ---
    chat = DeepSeekChatHandler(page)
    
    print("\n💬 Mode Chat Aktif. Ketik 'exit' untuk keluar.")
    
    while True:
        # Ambil input dari user secara async
        user_message = await asyncio.get_event_loop().run_in_executor(
            None, lambda: input("\n👤 Anda: ").strip()
        )
        
        if user_message.lower() in ['exit', 'quit', 'keluar']:
            break
            
        if not user_message:
            continue

        success = await chat.send_message(user_message)
        
        if success:
            # Tunggu respon AI
            await chat.wait_for_response()
            
            # Ambil hasil output
            response_text = await chat.get_latest_response()
            if response_text:
                print("\n🤖 AI:")
                print("-" * 30)
                print(response_text)
                print("-" * 30)
            else:
                print("⚠️  Gagal mengambil teks respon.")
        else:
            print("❌ Gagal mengirim pesan.")

    # --- CHAT INTERACTION END ---

    print("\nMenyimpan session dan menutup browser...")
    await app.close(save_before_close=True)

if __name__ == "__main__":
    asyncio.run(main())
