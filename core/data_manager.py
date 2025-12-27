import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from .models import Account, Session, Chat, Message
from datetime import datetime

class DataManager:
    def __init__(self, db: AsyncSession):
        self.db = db

    async def sync_account(self, email: str, password: str):
        """Pastikan akun ada di database"""
        stmt = select(Account).where(Account.email == email)
        result = await self.db.execute(stmt)
        account = result.scalar_one_or_none()
        
        if not account:
            account = Account(email=email, password=password)
            self.db.add(account)
            await self.db.commit()
            print(f"🆕 Account {email} created in DB")
        return account

    async def save_browser_session(self, session_id: str, account_email: str, storage_state: dict):
        """Simpan storage state browser (cookies, localstorage) ke DB"""
        stmt = select(Session).where(Session.session_id == session_id)
        result = await self.db.execute(stmt)
        db_session = result.scalar_one_or_none()
        
        if not db_session:
            db_session = Session(
                session_id=session_id,
                account_email=account_email,
                storage_state=storage_state
            )
            self.db.add(db_session)
        else:
            db_session.storage_state = storage_state
            db_session.updated_at = datetime.utcnow()
            
        await self.db.commit()
        print(f"💾 Browser session {session_id} saved to DB")

    async def get_browser_session(self, session_id: str):
        """Ambil storage state browser dari DB"""
        stmt = select(Session).where(Session.session_id == session_id)
        result = await self.db.execute(stmt)
        db_session = result.scalar_one_or_none()
        return db_session.storage_state if db_session else None

    async def save_chat_message(self, session_id: str, chat_id: str, role: str, content: str, image_url: str = None):
        """Simpan pesan chat ke DB"""
        # Pastikan session ada
        stmt_sess = select(Session).where(Session.session_id == session_id)
        res_sess = await self.db.execute(stmt_sess)
        if not res_sess.scalar_one_or_none():
            # Jika session belum ada di DB (misal baru mulai), buat dulu
            new_sess = Session(session_id=session_id)
            self.db.add(new_sess)
            await self.db.flush()

        # Pastikan chat record ada
        stmt_chat = select(Chat).where(Chat.chat_id == chat_id)
        res_chat = await self.db.execute(stmt_chat)
        db_chat = res_chat.scalar_one_or_none()
        
        if not db_chat:
            db_chat = Chat(chat_id=chat_id, session_id=session_id)
            self.db.add(db_chat)
            await self.db.flush()

        # Simpan pesan
        new_msg = Message(
            chat_id=chat_id,
            role=role,
            content=content,
            image_url=image_url
        )
        self.db.add(new_msg)
        await self.db.commit()

    async def update_chat_id(self, old_id: str, new_id: str):
        """Update chat_id dari UUID temporary ke ID asli DeepSeek menggunakan pola Create-Move-Delete"""
        if old_id == new_id:
            return

        # 1. Ambil data chat lama
        stmt_old = select(Chat).where(Chat.chat_id == old_id)
        res_old = await self.db.execute(stmt_old)
        old_chat = res_old.scalar_one_or_none()
        
        if not old_chat:
            return

        # 2. Cek apakah new_id sudah ada
        stmt_check = select(Chat).where(Chat.chat_id == new_id)
        res_check = await self.db.execute(stmt_check)
        new_chat = res_check.scalar_one_or_none()

        if not new_chat:
            # Buat chat baru dengan ID baru jika belum ada
            new_chat = Chat(
                chat_id=new_id,
                session_id=old_chat.session_id,
                title=old_chat.title,
                created_at=old_chat.created_at
            )
            self.db.add(new_chat)
            await self.db.flush() # Pastikan new_chat masuk ke DB agar FK Message valid

        # 3. Pindahkan semua pesan ke chat_id baru
        stmt_msgs = select(Message).where(Message.chat_id == old_id)
        res_msgs = await self.db.execute(stmt_msgs)
        for msg in res_msgs.scalars():
            msg.chat_id = new_id
        
        # 4. Hapus chat lama
        await self.db.delete(old_chat)
        
        await self.db.commit()
        print(f"🔄 Chat ID migrated from {old_id} to {new_id}")

    async def delete_chat(self, chat_id: str):
        """Hapus chat dan pesan terkait dari database"""
        # Hapus pesan dulu (opsional jika cascade delete sudah diatur di model, tapi cari aman)
        stmt_msgs = select(Message).where(Message.chat_id == chat_id)
        res_msgs = await self.db.execute(stmt_msgs)
        for msg in res_msgs.scalars():
            await self.db.delete(msg)
            
        stmt_chat = select(Chat).where(Chat.chat_id == chat_id)
        res_chat = await self.db.execute(stmt_chat)
        chat = res_chat.scalar_one_or_none()
        if chat:
            await self.db.delete(chat)
            await self.db.commit()
            print(f"🗑️  Chat {chat_id} dihapus dari DB (link tidak valid)")
