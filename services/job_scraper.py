import os
import re
import json
import requests
import logging
from typing import Dict, Optional
import numpy as np
from tqdm import tqdm
from google import genai
from firecrawl import FirecrawlApp
import aiohttp
import asyncio
from functools import partial
from concurrent.futures import ThreadPoolExecutor
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from database import AsyncSessionLocal, Job, JobSite
from schemas import JobCardPatternSchema, JobDetailPatternSchema, JobSchema
from prompt import JOB_CARD_PROMPT, JOB_EXTRACTION_PROMPT, REGEX_EXTRACTION_PROMPT
from config import settings

logger = logging.getLogger(__name__)

class JobScraper:
    def __init__(self):
        self.pages = {}
        self.regex_patterns = {}
        self.jobs = {}
        self.client = genai.Client(api_key=settings.GOOGLE_API_KEY)
        self.firecrawl_app = FirecrawlApp(api_key=settings.FIRECRAWL_API_KEY)
        self.loop = asyncio.get_event_loop()
        self.executor = ThreadPoolExecutor(max_workers=settings.THREAD_POOL_SIZE)
        logger.info(f"Initialized ThreadPoolExecutor with {settings.THREAD_POOL_SIZE} workers")

    async def get_db(self):
        async with AsyncSessionLocal() as session:
            return session

    async def get_or_create_job_site(self, url: str):
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(JobSite).filter(JobSite.url == url)
            )
            job_site = result.scalar_one_or_none()
            
            if not job_site:
                job_site = JobSite(url=url)
                db.add(job_site)
                await db.commit()
            return job_site

    async def update_job_site_patterns(self, job_site, patterns):
        logger.info(f"Updating job site patterns: {patterns}")

        async with AsyncSessionLocal() as db:
            job_site.regex_patterns = patterns
            db.add(job_site)
            await db.commit()
            await db.refresh(job_site)

    async def create_or_update_job(self, job_site, job_url, job_data, regex_patterns):
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).filter(Job.job_url == job_url)
            )
            job = result.scalar_one_or_none()
            
            job_dict = {
                'job_site_id': job_site.id,
                'job_url': job_url,
                'company_name': job_data.get('company_name'),
                'company_industry': job_data.get('company_industry'),
                'job_title': job_data.get('job_title'),
                'job_type': job_data.get('job_type'),
                'location': job_data.get('location'),
                'description': job_data.get('description'),
                'responsibilities': job_data.get('responsibilities'),
                'requirements': job_data.get('requirements'),
                'benefits': job_data.get('benefits'),
                'regex_patterns': regex_patterns
            }

            if job:
                for key, value in job_dict.items():
                    setattr(job, key, value)
            else:
                job = Job(**job_dict)
                db.add(job)
            
            await db.commit()
            return job

    async def get_page(self, url: str) -> Optional[str]:
        """Asynchronously fetch page content."""
        headers = {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        }
        
        # Use firecrawl to extract HTML
        try:
            response = await self.loop.run_in_executor(
                self.executor,
                lambda: self.firecrawl_app.scrape_url(
                    url=url,
                    params={'formats': ['html']}
                )
            )
            if not response or 'html' not in response:
                logger.error(f"Failed to fetch {url} using firecrawl")
                return None
            return response['html']
        except Exception as e:
            logger.error(f"Error fetching {url}: {str(e)}")
            return None

    async def analyze_structure_with_ai(self, prompt: str, schema: dict):
        """Asynchronously analyze page structure with AI."""
        generate_func = partial(
            self.client.models.generate_content,
            model="gemini-2.0-flash",
            contents=[prompt],
            config={
                'response_mime_type': 'application/json',
                'response_schema': schema,
                'max_output_tokens': 8192
            }
        )
        response = await self.loop.run_in_executor(self.executor, generate_func)
        return response.parsed.model_dump(mode='json')

    async def extract_job_data(self, url: str):
        """Asynchronously extract job data from a job posting page."""
        extract_func = partial(
            self.firecrawl_app.extract,
            [url],
            {
                'prompt': JOB_EXTRACTION_PROMPT,
                'schema': JobSchema.model_json_schema(),
            }
        )
        extracted_data = await self.loop.run_in_executor(self.executor, extract_func)
        return extracted_data

    async def fetch_job_details(self, url: str, job_url: str):
        logger.info(f"Fetching job details from {job_url}")
        html = await self.get_page(job_url)
        if not html:
            logger.error(f"Failed to fetch {job_url}")
            return

        job_site = await self.get_or_create_job_site(url)
        
        # Check if job exists and has regex patterns
        async with AsyncSessionLocal() as db:
            result = await db.execute(
                select(Job).filter(Job.job_url == job_url)
            )
            existing_job = result.scalar_one_or_none()

        if existing_job and existing_job.regex_patterns:
            regex_pattern = existing_job.regex_patterns
        else:
            job_data = await self.extract_job_data(job_url)

            if job_data['success'] == False:
                logger.error(f"Failed to extract job data from {job_url}")
                return
            
            extracted_data = job_data['data']
            
            if self.jobs.get(url) is None:
                self.jobs[url] = {}
            
            self.jobs[url][job_url] = extracted_data

            prompt = REGEX_EXTRACTION_PROMPT.format(
                extracted_data=json.dumps(extracted_data),
                html=html
            )
            regex_pattern = await self.analyze_structure_with_ai(prompt, JobDetailPatternSchema)
            
            # Save to database
            await self.create_or_update_job(job_site, job_url, extracted_data, regex_pattern)

    async def fetch_jobs(self, url: str, 
                        batch_size: int, 
                        max_concurrent: int):
        """Asynchronously fetch job postings from a career page."""
        html = await self.get_page(url)
        
        if not html:
            logger.error(f"Failed to fetch {url}")
            return
        
        self.pages[url] = html
        job_site = await self.get_or_create_job_site(url)

        # If regex is not stored, analyze the structure with AI
        db = await self.get_db()
        result = await db.execute(
            select(JobSite.regex_patterns).filter(JobSite.id == job_site.id)
        )
        stored_patterns = result.scalar_one_or_none()
        
        if not stored_patterns:
            prompt = JOB_CARD_PROMPT.format(markdown=html)
            job_card_pattern = await self.analyze_structure_with_ai(prompt, JobCardPatternSchema)
            self.regex_patterns[url] = job_card_pattern
            
            await self.update_job_site_patterns(job_site, self.regex_patterns[url].get('job_url_pattern', {}))
        else:
            self.regex_patterns[url] = stored_patterns

        regex_pattern = self.regex_patterns[url]['job_url_pattern']['pattern']
        logger.info(f"Regex pattern: {regex_pattern}")
        job_urls = re.findall(regex_pattern, html)
        job_urls = np.unique(job_urls).tolist()

        # Create a semaphore to limit concurrent requests
        semaphore = asyncio.Semaphore(max_concurrent)

        async def process_job_with_semaphore(job_url: str):
            async with semaphore:
                full_url = job_url if job_url.startswith("http") else url + job_url
                await self.fetch_job_details(url, full_url)

        # Modify batch size to respect rate limit (10 requests per minute)
        rate_limit_batch_size = min(batch_size, 10)  # Never process more than 10 at once
        
        total_processed = 0
        logger.info(f"Found {len(job_urls)} jobs to process")

        for i in range(0, len(job_urls), rate_limit_batch_size):
            batch = job_urls[i:i + rate_limit_batch_size]
            tasks = [process_job_with_semaphore(job_url) for job_url in batch]
            
            try:
                await asyncio.gather(*tasks)
                total_processed += len(batch)
                logger.info(f"Processed {total_processed}/{len(job_urls)} jobs")
            except Exception as e:
                logger.error(f"Error processing batch {i//rate_limit_batch_size + 1}: {str(e)}")
                continue

            # Wait for 60 seconds after each batch to respect the rate limit
            logger.info("Waiting 60 seconds to respect rate limit...")
            await asyncio.sleep(60)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        if self.executor:
            self.executor.shutdown(wait=True)
        if self.client:
            await self.client.close()
        if self.firecrawl_app:
            await self.firecrawl_app.close()

# Create a singleton instance
job_scraper = JobScraper()