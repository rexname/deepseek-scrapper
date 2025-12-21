import asyncio
from playwright.async_api import Page, TimeoutError
import time

class DeepSeekChatHandler:
    def __init__(self, page: Page):
        self.page = page
        # Selectors based on DeepSeek UI structure
        self.selectors = {
            "input_area": "textarea#chat-input",
            "send_button": "button[title='Send Message'], .ds-icon-send, button:has(svg)",
            "message_list": ".ds-markdown.ds-markdown--block",
            "is_generating": ".ds-stop-button, .ds-icon-stop, button:has(.ds-icon-stop)",
            "thinking_area": ".ds-base-expand",
            "chat_container": "xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[2]/div[1]/div[2]"
        }
        
        # Base XPath untuk pesan (input dan output)
        self.base_message_xpath = "//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[2]/div[1]/div[2]/div[1]"
        
        # Pola bubble chat: base_message_xpath/div[n]/div[1]/div[1]
        # n ganjil = user, n genap = AI
        self.bubble_content_path = "./div[1]/div[1]"
        
        # Override input_area jika XPath spesifik diberikan
        self.xpath_input_initial = "xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[1]/div[2]/div[2]/div[1]/div[1]/div[1]/textarea[1]"
        self.xpath_input_active = "xpath=//*[@id=\"root\"]/div[1]/div[1]/div[2]/div[3]/div[1]/div[2]/div[1]/div[2]/div[2]/div[2]/div[1]/div[1]/div[1]/textarea[1]"

    async def send_message(self, message: str, timeout: int = 30000):
        """Mengirim pesan ke chat"""
        try:
            # 1. Coba cari input area dengan berbagai strategi
            input_selectors = [
                self.xpath_input_active,
                self.xpath_input_initial,
                "xpath=//textarea[@id='chat-input']",
                "textarea#chat-input",
                "textarea[placeholder*='chat']",
                "textarea"
            ]
            
            target_selector = None
            for selector in input_selectors:
                if not selector: continue
                try:
                    await self.page.wait_for_selector(selector, state="visible", timeout=2000)
                    target_selector = selector
                    break
                except:
                    continue
            
            if not target_selector:
                print("❌ Gagal menemukan input area dengan selector manapun.")
                return False

            # 2. Fokus dan isi pesan
            await self.page.focus(target_selector)
            await self.page.fill(target_selector, message)
            await asyncio.sleep(0.5) 
            
            # 3. Klik tombol kirim dengan berbagai strategi
            send_selectors = [
                self.selectors["send_button"],
                "xpath=//div[@role='button' and @aria-label='Send message']",
                "button[title='Send Message']",
                ".ds-icon-send",
                "button:has(svg)"
            ]
            
            sent = False
            for selector in send_selectors:
                button = await self.page.query_selector(selector)
                if button and await button.is_enabled():
                    await button.click()
                    sent = True
                    break
            
            if not sent:
                # Fallback ke Enter
                await self.page.press(target_selector, "Enter")
                
            print(f"✉️  Pesan dikirim: {message[:50]}...")
            return True
        except Exception as e:
            print(f"❌ Gagal mengirim pesan: {e}")
            return False

    async def wait_for_response(self, timeout: int = 120000, stability_check: bool = True):
        """Menunggu sampai AI selesai memberikan respon"""
        print("⏳ Menunggu respon AI...")
        start_time = time.time()
        last_text = ""
        stable_count = 0
        
        try:
            # Tunggu sebentar agar UI update status ke 'generating'
            await asyncio.sleep(2) 
            
            while time.time() - start_time < (timeout / 1000):
                # Cek apakah indikator stop/generating masih ada
                is_generating = await self.page.is_visible(self.selectors["is_generating"])
                
                if not is_generating:
                    if not stability_check:
                        break
                    
                    # Cek stabilitas teks untuk memastikan benar-benar selesai
                    current_text = await self.get_latest_response() or ""
                    if current_text and current_text == last_text:
                        stable_count += 1
                    else:
                        stable_count = 0
                        last_text = current_text
                    
                    if stable_count >= 2: # 2 detik stabil tanpa indikator generating
                        break
                
                await asyncio.sleep(1)
            
            print("✅ AI selesai merespon.")
            return True
        except Exception as e:
            print(f"⚠️  Error saat menunggu respon: {e}")
            return False

    async def get_latest_response(self):
        """Mengambil teks dari bubble chat terakhir menggunakan pola XPath dinamis"""
        try:
            # 1. Coba ambil bubble dengan XPath spesifik user (paling akurat untuk UI utama)
            bubbles = await self.page.query_selector_all(f"xpath={self.base_message_xpath}/div")
            
            # 2. Jika gagal, coba cari container chat yang mungkin berbeda strukturnya
            if not bubbles:
                alternative_containers = [
                    "xpath=//div[contains(@class, 'chat-container')]//div[contains(@class, 'message')]",
                    "xpath=//div[contains(@class, 'ds-markdown')]/ancestor::div[1]",
                    ".ds-markdown.ds-markdown--block"
                ]
                for container_selector in alternative_containers:
                    bubbles = await self.page.query_selector_all(container_selector)
                    if bubbles: break

            if not bubbles:
                return None

            # 3. Ambil bubble terakhir (biasanya AI)
            last_bubble = bubbles[-1]
            
            # 4. Ambil konten (Coba pola nested user dulu, lalu fallback)
            content_element = await last_bubble.query_selector(f"xpath={self.bubble_content_path}")
            
            if content_element:
                markdown_element = await content_element.query_selector(self.selectors["message_list"])
                if markdown_element:
                    return (await markdown_element.inner_text()).strip()
                return (await content_element.inner_text()).strip()
            
            # Fallback ke inner_text dari bubble itu sendiri
            # Cek apakah bubble itu sendiri mengandung markdown
            markdown_element = await last_bubble.query_selector(self.selectors["message_list"])
            if markdown_element:
                return (await markdown_element.inner_text()).strip()
                
            return (await last_bubble.inner_text()).strip()
            
        except Exception as e:
            print(f"❌ Gagal mengambil respon: {e}")
            return None
