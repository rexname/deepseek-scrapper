import asyncio
from core.database import engine, Base
from core.models import Session, Chat, Message

async def init_db():
    async with engine.begin() as conn:
        print("🚀 Initializing database tables...")
        # await conn.run_sync(Base.metadata.drop_all) # Gunakan ini jika ingin reset
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables created successfully.")

if __name__ == "__main__":
    asyncio.run(init_db())
