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
        self.browserless_url = browserless_url.rstrip('/')
        self.site_name = site_name
        self.api_token = api_token
        self.session_id = session_id or hashlib.md5(f"{site_name}".encode()).hexdigest()[:12]
        
        self.playwright = None
        self.browser: Optional[Browser] = None
        self.context: Optional[BrowserContext] = None
        self.page: Optional[Page] = None
        
        print(f"🚀 Browserless Session Manager: {site_name}")
        print(f"   Session ID: {self.session_id}")
    
    async def connect_browserless(self, storage_state: dict = None):
        """Connect ke Browserless dengan session persisten"""
        try:
            self.playwright = await async_playwright().start()
            
            params = [
                f"sessionId={self.session_id}",
                "keepAlive=true",
                f"--user-data-dir=/tmp/session-{self.session_id}"
            ]
            if self.api_token:
                params.append(f"token={self.api_token}")
            
            connect_url = f"{self.browserless_url}/?{'&'.join(params)}"
            
            print(f"🔗 Connecting to Browserless: {self.browserless_url}")
            self.browser = await self.playwright.chromium.connect_over_cdp(connect_url)
            
            self.context = await self.browser.new_context(
                viewport={'width': 1920, 'height': 1080},
                user_agent='Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36',
                ignore_https_errors=True,
                storage_state=storage_state
            )
            
            print("✅ Connected and Context created")
            return True
            
        except Exception as e:
            print(f"❌ Failed to connect to Browserless: {e}")
            return False
    
    async def get_storage_state(self):
        """Ambil storage state saat ini dari context"""
        if self.context:
            return await self.context.storage_state()
        return None

    async def new_page(self) -> Page:
        if not self.context:
            raise Exception("Browser not connected")
        self.page = await self.context.new_page()
        return self.page
    
    async def close(self, save_before_close: bool = False):
        try:
            if self.context:
                await self.context.close()
            if self.browser:
                await self.browser.close()
            if self.playwright:
                await self.playwright.stop()
            print("👋 Browserless connection closed")
        except Exception as e:
            print(f"⚠️ Error while closing: {e}")
