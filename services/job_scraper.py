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
from schemas import JobDetailPatternSchema, JobSchema, JobSiteAnalysis, PaginationType, PaginationPattern
from prompt import JOB_EXTRACTION_PROMPT, REGEX_EXTRACTION_PROMPT, SITE_ANALYSIS_PROMPT
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

    async def update_job_site_patterns(self, job_site, site_analysis):
        logger.info(f"Updating job site patterns and pagination info: {site_analysis}")

        async with AsyncSessionLocal() as db:
            job_site.regex_patterns = site_analysis['job_url_pattern']
            job_site.pagination_type = site_analysis['pagination']['type']
            job_site.pagination_info = site_analysis['pagination']
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

    async def analyze_site_structure(self, url: str, html: str) -> JobSiteAnalysis:
        """Analyze both job cards and pagination in a single LLM call."""
        prompt = SITE_ANALYSIS_PROMPT.format(markdown=html)
        site_analysis = await self.analyze_structure_with_ai(
            prompt, 
            JobSiteAnalysis
        )
        logger.info(f"Analyzed site structure - Pagination type: {site_analysis['pagination']['type']}")
        return site_analysis

    async def fetch_jobs(self, url: str, 
                        batch_size: int, 
                        max_concurrent: int,
                        max_pages: int = 10):
        """Asynchronously fetch job postings from a career page."""
        all_job_urls = set()
        
        # Get initial page and analyze structure
        initial_html = await self.get_page(url)
        if not initial_html:
            logger.error(f"Failed to fetch initial page from {url}")
            return
            
        self.pages[url] = initial_html
        job_site = await self.get_or_create_job_site(url)

        # Get or analyze site structure
        db = await self.get_db()
        result = await db.execute(
            select(JobSite.regex_patterns).filter(JobSite.id == job_site.id)
        )
        stored_patterns = result.scalar_one_or_none()
        
        if not stored_patterns:
            site_analysis = await self.analyze_site_structure(url, initial_html)
            self.regex_patterns[url] = site_analysis['job_url_pattern']
            await self.update_job_site_patterns(job_site, site_analysis)
            pagination_info = PaginationPattern(**site_analysis['pagination'])
        else:
            self.regex_patterns[url] = stored_patterns
            # Use stored pagination info if available, otherwise re-analyze
            if hasattr(job_site, 'pagination_info') and job_site.pagination_info:
                pagination_info = PaginationPattern(**job_site.pagination_info)
            else:
                site_analysis = await self.analyze_site_structure(url, initial_html)
                pagination_info = site_analysis['pagination']
                await self.update_job_site_patterns(job_site, site_analysis)

        regex_pattern = self.regex_patterns[url]['pattern']
        
        # Process first page
        new_job_urls = set(re.findall(regex_pattern, initial_html))
        all_job_urls.update(new_job_urls)
        
        # Handle pagination based on detected type
        page = 2
        while page <= max_pages:
            if pagination_info.type == PaginationType.NONE:
                logger.info("No pagination detected")
                break
                
            # Get next page content based on pagination type
            if pagination_info.type == PaginationType.PAGE_NUMBERS:
                page_param = pagination_info.page_param or "page"
                paginated_url = (f"{url}&{page_param}={page}" 
                               if "?" in url else f"{url}?{page_param}={page}")
                html = await self.get_page(paginated_url)
            elif pagination_info.type in [PaginationType.LOAD_MORE, PaginationType.INFINITE_SCROLL]:
                if pagination_info.next_page_pattern.pattern:
                    api_url = pagination_info.next_page_pattern.pattern.format(
                        page=page,
                        offset=(page - 1) * pagination_info.items_per_page
                    )
                    html = await self.get_page(api_url)
                else:
                    logger.error(f"No next page pattern found for {url}")
                    break
            
            if not html:
                logger.error(f"Failed to fetch page {page}")
                break
                
            new_job_urls = set(re.findall(regex_pattern, html))
            
            if not new_job_urls or new_job_urls.issubset(all_job_urls):
                logger.info(f"No new jobs found on page {page}, stopping pagination")
                break
                
            all_job_urls.update(new_job_urls)
            logger.info(f"Found {len(new_job_urls)} new jobs on page {page}")
            page += 1
            
            if pagination_info.type in [PaginationType.LOAD_MORE, PaginationType.INFINITE_SCROLL]:
                await asyncio.sleep(2)

        # Process found jobs (rest of the code remains the same)
        job_urls = list(all_job_urls)
        semaphore = asyncio.Semaphore(max_concurrent)
        
        async def process_job_with_semaphore(job_url: str):
            async with semaphore:
                full_url = job_url if job_url.startswith("http") else url + job_url
                await self.fetch_job_details(url, full_url)

        rate_limit_batch_size = min(batch_size, 10)
        total_processed = 0
        logger.info(f"Found total of {len(job_urls)} jobs to process")

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

            logger.info("Waiting 60 seconds to respect rate limit...")
            await asyncio.sleep(60)

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        await self.close()

    async def close(self):
        if self.executor:
            self.executor.shutdown(wait=True)

# Create a singleton instance
job_scraper = JobScraper()