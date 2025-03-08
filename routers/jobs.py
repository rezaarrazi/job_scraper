from fastapi import APIRouter, HTTPException, Query
from typing import Dict, List
import logging
from sqlalchemy import select

from database import AsyncSessionLocal, Job
from schemas import JobResponse
from services.job_scraper import job_scraper

logger = logging.getLogger(__name__)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"]
)

@router.post("/scrape")
async def scrape_job(
    url: Dict[str, str],
    batch_size: int = Query(default=50, gt=0, le=100),
    max_concurrent: int = Query(default=10, gt=0, le=20)
):
    try:
        if not url.get("url"):
            raise HTTPException(status_code=400, detail="URL is required")
        
        logger.info(f"Received request to scrape job at: {url['url']}")
        
        await job_scraper.fetch_jobs(
            url["url"],
            batch_size=batch_size,
            max_concurrent=max_concurrent
        )
        logger.info("Successfully structured job data")
        
        return {"message": "Job data scraped and stored successfully"}
    
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/", response_model=Dict[str, List[JobResponse]])
async def get_jobs():
    try:
        async with AsyncSessionLocal() as db:
            result = await db.execute(select(Job))
            jobs = result.scalars().all()
            
            job_list = []
            for job in jobs:
                job_list.append({
                    'id': job.id,
                    'job_url': job.job_url,
                    'company_name': job.company_name,
                    'job_title': job.job_title,
                    'location': job.location,
                    'created_at': job.created_at,
                    'updated_at': job.updated_at
                })
            
            return {"jobs": job_list}
    
    except Exception as e:
        logger.error(f"Error fetching jobs: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e)) 