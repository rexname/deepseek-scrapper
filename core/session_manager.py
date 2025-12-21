import asyncio
import os
import json
from playwright.async_api import async_playwright, Browser, BrowserContext, Page
from typing import Optional
from datetime import datetime
import hashlib
from pathlib import Path

class BrowserlessSessionManager:
    """
    Session Manager khusus untuk Browserless.io dengan standard persistence
    """
    
    def __init__(self, 
                 browserless_url: str,
                 site_name: str,
                 session_dir: str = "sessions",
                 api_token: Optional[str] = None,
                 session_id: Optional[str] = None):
        """
        Args:
            browserless_url: URL Browserless (ws:// atau wss://)
            site_name: Nama website untuk identifikasi session
            session_dir: Folder penyimpanan session lokal
            api_token: Token untuk Browserless (jika ada)
            session_id: ID session untuk persistence di Browserless
        """
        self.browserless_url = browserless_url.rstrip('/')
        self.site_name = site_name
        self.api_token = api_token
        self.session_dir = Path(session_dir)
        self.session_dir.mkdir(exist_ok=True)
        
        # Fixed session ID atau generate dari site_name
        self.session_id = session_id or hashlib.md5(f"{site_name}".encode()).hexdigest()[:12]
        
        # Local state files
        self.session_file = self.session_dir / f"{site_name}_meta.json"
        self.storage_file = self.session_dir / f"{site_name}_storage.json"
        
        # Browser objects
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        print(f"🚀 Browserless Session Manager: {site_name}")
        print(f"   Session ID: {self.session_id}")
    
    async def connect_browserless(self):
        """Connect ke Browserless dengan session persisten menggunakan standard sessionId & userDataDir"""
        try:
            self.playwright = await async_playwright().start()
            
            # Construct connection URL with parameters for persistence
            # Menggunakan standard Browserless params: sessionId dan --user-data-dir
            params = [
                f"sessionId={self.session_id}",
                "keepAlive=true",
                f"--user-data-dir=/tmp/session-{self.session_id}"
            ]
            if self.api_token:
                params.append(f"token={self.api_token}")
            
            # Browserless standard endpoint untuk CDP
            connect_url = f"{self.browserless_url}/chromium?{'&'.join(params)}"
            
            print(f"🔗 Connecting to Browserless: {self.browserless_url}")
            print(f"   Session ID: {self.session_id}")
            
            self.browser = await self.playwright.chromium.connect_over_cdp(connect_url)
            
            # Load storage state jika ada untuk konsistensi lokal
            storage_state = None
            if self.storage_file.exists():
                print(f"� Loading existing storage state from {self.storage_file}")
                with open(self.storage_file, 'r') as f:
                    storage_state = json.load(f)

            # Create context dengan storage_state jika tersedia
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                ignore_https_errors=True,
                storage_state=storage_state
            )
            
            print("✅ Connected and Context created with persistence")
            return True
            
        except Exception as e:
            print(f"❌ Failed to connect to Browserless: {e}")
            return False
    
    async def new_page(self) -> Page:
        """Buat page baru"""
        if not self.context:
            raise Exception("Browser not connected. Call connect_browserless() first")
        
        self.page = await self.context.new_page()
        return self.page
    
    async def save_session(self):
        """Simpan storage state ke file (standard Playwright way)"""
        try:
            if not self.context:
                print("⚠️ No active context to save")
                return False
            
            # Playwright storage state includes cookies and local storage
            storage_state = await self.context.storage_state()
            with open(self.storage_file, 'w') as f:
                json.dump(storage_state, f, indent=2)
            
            # Simpan metadata tambahan jika perlu
            cookies = storage_state.get('cookies', [])
            session_data = {
                'site_name': self.site_name,
                'session_id': self.session_id,
                'last_saved': datetime.now().isoformat(),
                'cookies_count': len(cookies)
            }
            with open(self.session_file, 'w') as f:
                json.dump(session_data, f, indent=2)
            
            print(f"💾 Session saved to {self.storage_file} ({len(cookies)} cookies)")
            return True
            
        except Exception as e:
            print(f"❌ Failed to save session: {e}")
            return False
    
    async def load_session(self) -> bool:
        """
        Load session dari file. 
        Note: Di Browserless, persistence biasanya ditangani oleh server via userDataDir.
        Method ini berguna untuk sinkronisasi state lokal ke context baru.
        """
        try:
            if not self.storage_file.exists():
                print("⚠️ No saved storage state found")
                return False
            
            # Kita tidak perlu manual load jika sudah di-pass saat new_context
            # Tapi method ini bisa dipanggil untuk refresh context yang sudah ada
            with open(self.storage_file, 'r') as f:
                storage_state = json.load(f)
            
            # Apply cookies
            if 'cookies' in storage_state:
                await self.context.add_cookies(storage_state['cookies'])
            
            print(f"✅ Session loaded from {self.storage_file}")
            return True
            
        except Exception as e:
            print(f"❌ Failed to load session: {e}")
            return False
    
    async def open_with_session(self, url: str, wait_until: str = "domcontentloaded"):
        if not self.context:
            await self.connect_browserless()
        if not self.page:
            self.page = await self.new_page()
        await self.page.goto(url, wait_until=wait_until)
        return self.page

    async def close(self, save_before_close: bool = True):
        """Close browser dan bersihkan resources"""
        try:
            if save_before_close and self.context:
                await self.save_session()
            
            if self.context:
                await self.context.close()
            
            if self.browser:
                await self.browser.close()
            
            if self.playwright:
                await self.playwright.stop()
            
            print("👋 Browserless connection closed")
            
        except Exception as e:
            print(f"⚠️ Error while closing: {e}")
    
    def get_session_info(self):
        """Get informasi session"""
        info = {
            'site_name': self.site_name,
            'session_id': self.session_id,
            'browserless_url': self.browserless_url,
            'storage_file': str(self.storage_file),
            'has_saved_session': self.storage_file.exists()
        }
        
        if self.session_file.exists():
            with open(self.session_file, 'r') as f:
                info['metadata'] = json.load(f)
        
        return info
