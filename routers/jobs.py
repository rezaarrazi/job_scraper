from fastapi import APIRouter, HTTPException, Query
from typing import Dict, List
from sqlalchemy import select
from uuid import UUID

from database import AsyncSessionLocal, Job
from schemas import JobResponse, ScrapeResponse, ScrapeStatusResponse
from services.job_scraper import job_scraper
from utils.logger import setup_logger

logger = setup_logger(__name__)

router = APIRouter(
    prefix="/jobs",
    tags=["jobs"]
)

@router.post("/scrape", status_code=202)
async def scrape_job(
    url: Dict[str, str],
    batch_size: int = Query(default=10, gt=0, le=100),
    max_concurrent: int = Query(default=5, gt=0, le=20),
    max_pages: int = Query(default=10, gt=0, le=100)
):
    try:
        if not url.get("url"):
            raise HTTPException(status_code=400, detail="URL is required")
        
        logger.info(f"Received request to scrape job at: {url['url']}")
        
        task_id = await job_scraper.start_scraping(
            url["url"],
            batch_size=batch_size,
            max_concurrent=max_concurrent,
            max_pages=max_pages
        )
        
        return {"task_id": task_id}
    
    except Exception as e:
        logger.error(f"Error processing request: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

@router.get("/scrape/{task_id}/status", response_model=ScrapeStatusResponse)
async def get_scrape_status(task_id: str):
    try:
        task = await job_scraper.get_task_status(task_id)
        if not task:
            raise HTTPException(status_code=404, detail="Task not found")
            
        return {
            "task_id": task.id,
            "status": task.status,
            "progress": task.progress,
            "error": task.error,
            "started_at": task.started_at,
            "completed_at": task.completed_at
        }
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Error fetching task status: {str(e)}")
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
                    'id': str(job.id),
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