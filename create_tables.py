import asyncio
from database import reset_db

async def main():
    await reset_db()
    print("Database has been reset and tables recreated successfully!")

if __name__ == "__main__":
    asyncio.run(main())