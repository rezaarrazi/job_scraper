from fastapi import FastAPI
import logging
from dotenv import load_dotenv
import asyncio

from database import init_db
from routers.jobs import router as jobs_router

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

load_dotenv()

app = FastAPI(title="Job Scraper API")

@app.on_event("startup")
async def startup_event():
    """Initialize database on startup"""
    await init_db()

# Include routers
app.include_router(jobs_router)

if __name__ == '__main__':
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=5000)
