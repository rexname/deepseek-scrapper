import asyncio
from core.database import engine, Base
from core.models import Account, Session, Chat, Message

async def init_db(reset=False):
    async with engine.begin() as conn:
        if reset:
            print("⚠️ Resetting database (dropping all tables)...")
            await conn.run_sync(Base.metadata.drop_all)
        
        print("🚀 Syncing database tables...")
        await conn.run_sync(Base.metadata.create_all)
        print("✅ Database tables synced successfully.")

if __name__ == "__main__":
    import sys
    reset_db = "--reset" in sys.argv
    asyncio.run(init_db(reset=reset_db))
