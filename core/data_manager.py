import json
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.future import select
from sqlalchemy import or_
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

    async def save_chat_message(self, session_id: str, chat_id: str, role: str, content: str, image_url: str = None, account_email: str = None):
        """Simpan pesan chat ke DB"""
        # Pastikan session ada
        stmt_sess = select(Session).where(Session.session_id == session_id)
        res_sess = await self.db.execute(stmt_sess)
        db_sess = res_sess.scalar_one_or_none()
        
        if not db_sess:
            # Jika session belum ada di DB (misal baru mulai), buat dulu
            db_sess = Session(session_id=session_id, account_email=account_email)
            self.db.add(db_sess)
            await self.db.flush()
        elif account_email and not db_sess.account_email:
            # Update email jika belum ada
            db_sess.account_email = account_email
            await self.db.flush()

        # Ambil email dari session jika tidak disediakan langsung
        final_email = account_email or db_sess.account_email

        # Pastikan chat record ada
        stmt_chat = select(Chat).where(or_(Chat.chat_id == chat_id, Chat.id == chat_id))
        res_chat = await self.db.execute(stmt_chat)
        db_chat = res_chat.scalar_one_or_none()
        
        if not db_chat:
            db_chat = Chat(chat_id=chat_id, session_id=session_id, account_email=final_email)
            self.db.add(db_chat)
            await self.db.flush()
        elif final_email and not db_chat.account_email:
            db_chat.account_email = final_email
            await self.db.flush()

        # Simpan pesan - SELALU gunakan db_chat.id (PK) sebagai foreign key
        new_msg = Message(
            chat_id=db_chat.id,
            role=role,
            content=content,
            image_url=image_url
        )
        self.db.add(new_msg)
        await self.db.commit()

    async def update_chat_id(self, old_id: str, new_id: str):
        """Update chat_id dari UUID temporary ke ID asli DeepSeek"""
        if old_id == new_id:
            return

        # 1. Ambil data chat lama (cari berdasarkan chat_id atau id)
        stmt_old = select(Chat).where(or_(Chat.chat_id == old_id, Chat.id == old_id))
        res_old = await self.db.execute(stmt_old)
        old_chat = res_old.scalar_one_or_none()
        
        if not old_chat:
            return

        # 2. Cek apakah new_id sudah ada di record lain
        stmt_check = select(Chat).where(Chat.chat_id == new_id)
        res_check = await self.db.execute(stmt_check)
        existing_chat = res_check.scalar_one_or_none()

        if existing_chat:
            # Jika sudah ada chat dengan new_id tersebut, kita harus MERGE.
            # Karena Message.chat_id mereferensikan Chat.id (UUID),
            # kita pindahkan semua pesan dari old_chat.id ke existing_chat.id.
            if existing_chat.id != old_chat.id:
                stmt_msgs = select(Message).where(Message.chat_id == old_chat.id)
                res_msgs = await self.db.execute(stmt_msgs)
                for msg in res_msgs.scalars():
                    msg.chat_id = existing_chat.id
                
                # Hapus chat lama
                await self.db.delete(old_chat)
                print(f"🔗 Merged chat {old_id} into existing chat {new_id}")
        else:
            # Jika belum ada, cukup update chat_id di record yang sekarang
            old_chat.chat_id = new_id
            print(f"🔄 Updated chat_id from {old_id} to {new_id}")
        
        await self.db.commit()

    async def update_chat_title(self, chat_id: str, title: str):
        """Update judul chat di database"""
        if not title:
            return
            
        stmt = select(Chat).where(or_(Chat.chat_id == chat_id, Chat.id == chat_id))
        res = await self.db.execute(stmt)
        chat = res.scalar_one_or_none()
        if chat:
            chat.title = title
            await self.db.commit()
            print(f"📝 Title updated for chat {chat_id}: {title}")

    async def get_chats(self, session_id: str = None, account_email: str = None):
        """Ambil daftar chat untuk session atau account tertentu"""
        stmt = select(Chat)
        if session_id:
            stmt = stmt.where(Chat.session_id == session_id)
        if account_email:
            stmt = stmt.where(Chat.account_email == account_email)
            
        stmt = stmt.order_by(Chat.created_at.desc())
        res = await self.db.execute(stmt)
        return res.scalars().all()

    async def delete_chat(self, chat_id: str):
        """Hapus chat dan pesan terkait dari database"""
        stmt_chat = select(Chat).where(or_(Chat.chat_id == chat_id, Chat.id == chat_id))
        res_chat = await self.db.execute(stmt_chat)
        chat = res_chat.scalar_one_or_none()
        if chat:
            await self.db.delete(chat)
            await self.db.commit()
            print(f"🗑️  Chat {chat_id} dihapus dari DB (link tidak valid)")
