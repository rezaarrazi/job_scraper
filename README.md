# Job Scraper

A powerful and intelligent job scraping system that uses AI to automatically analyze and extract job postings from career websites. The system leverages Google's Gemini AI and Firecrawl for intelligent HTML parsing and data extraction.

## Features

- 🤖 AI-powered job site structure analysis
- 🔄 Automatic pagination detection and handling
- 🎯 Smart job detail extraction
- 💾 Database storage with SQLAlchemy
- ⚡ Asynchronous processing for better performance
- 🔒 Rate limiting and respectful crawling
- 🎭 Multiple pagination types support (Page Numbers, Load More, Infinite Scroll)

## Prerequisites

- Python 3.8+
- PostgreSQL database
- Google AI API key (Gemini)
- Firecrawl API key

## Installation

1. Clone the repository:
```bash
git clone <repository-url>
cd job_scrapper
```

2. Install dependencies:
```bash
pip install -r requirements.txt
```

3. Set up environment variables:
```bash
# Create a .env file with the following variables
GOOGLE_API_KEY=your_google_api_key
FIRECRAWL_API_KEY=your_firecrawl_api_key
DATABASE_URL=postgresql+asyncpg://user:password@localhost/db_name
THREAD_POOL_SIZE=4  # Adjust based on your needs
```

## How It Works

### 1. Site Analysis
The scraper first analyzes the career site's structure using AI to:
- Detect job listing patterns
- Identify pagination type and structure
- Extract relevant URL patterns

### 2. Job Discovery
- Crawls through job listing pages
- Handles different types of pagination:
  - Traditional page numbers
  - "Load More" buttons
  - Infinite scroll
- Collects all job posting URLs

### 3. Data Extraction
For each job posting:
- Fetches the job detail page
- Uses AI to extract structured data including:
  - Job title
  - Company name
  - Location
  - Job type
  - Description
  - Requirements
  - Benefits
- Generates regex patterns for future scraping

### 4. Data Storage
- Stores all extracted data in a PostgreSQL database
- Maintains job site metadata and patterns
- Updates existing entries when re-scraping

## Usage

There are two ways to use this project:

### 1. As a Script

Create a Python script (e.g., `run_scraper.py`):

```python
import asyncio
from services.job_scraper import job_scraper

async def main():
    url = "https://example.com/careers"
    async with job_scraper:
        await job_scraper.fetch_jobs(
            task_id="manual-run",  # For script usage
            url=url,
            batch_size=10,  # Number of jobs to process in each batch
            max_concurrent=5,  # Maximum concurrent requests
            max_pages=10  # Maximum number of pages to scrape
        )

if __name__ == "__main__":
    asyncio.run(main())
```

Run the script:
```bash
python run_scraper.py
```

### 2. As an API Service

The project can be run as a FastAPI service, providing RESTful endpoints to trigger and monitor scraping jobs.

#### Starting the API Server

1. Install additional dependencies:
```bash
pip install fastapi uvicorn
```

2. Run the API server:
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

#### API Endpoints

##### Start a Scraping Job
```http
POST /jobs/scrape
Content-Type: application/json

{
    "url": "https://example.com/careers",
    "batch_size": 10,
    "max_concurrent": 5,
    "max_pages": 10
}
```

Response:
```json
{
    "task_id": "uuid-task-id"
}
```

##### Check Scraping Status
```http
GET /jobs/scrape/{task_id}/status
```

Response:
```json
{
    "task_id": "uuid-task-id",
    "status": "running",
    "progress": 45,
    "error": null,
    "started_at": "2024-03-14T12:00:00Z",
    "completed_at": null
}
```

##### Get All Scraped Jobs
```http
GET /jobs
```

Response:
```json
{
    "jobs": [
        {
            "id": "uuid",
            "job_url": "https://example.com/careers/job-1",
            "company_name": "Example Corp",
            "job_title": "Software Engineer",
            "location": "Remote",
            "created_at": "2024-03-14T12:00:00Z",
            "updated_at": "2024-03-14T12:00:00Z"
        }
    ]
}
```

## Configuration Options

- `batch_size`: Control how many jobs are processed in each batch
- `max_concurrent`: Limit concurrent requests to respect server limits
- `max_pages`: Set maximum number of pages to scrape
- `THREAD_POOL_SIZE`: Configure number of worker threads (via environment variable)

## Database Schema

The system uses two main tables:
1. `job_sites`: Stores information about career sites and their structure
2. `jobs`: Stores detailed job posting information

## Best Practices

- Respect robots.txt and site-specific crawling rules
- Implement appropriate delays between requests (built-in 60-second delay between batches)
- Use appropriate User-Agent headers
- Monitor and handle rate limits

## Error Handling

The system includes:
- Comprehensive error logging
- Failed request handling
- Rate limit management
- Automatic retries for failed requests

## Running the Project

There are two ways to use this project:

### 1. As a Script

Create a Python script (e.g., `run_scraper.py`):

```python
import asyncio
from services.job_scraper import job_scraper

async def main():
    url = "https://example.com/careers"
    async with job_scraper:
        await job_scraper.fetch_jobs(
            task_id="manual-run",  # For script usage
            url=url,
            batch_size=10,  # Number of jobs to process in each batch
            max_concurrent=5,  # Maximum concurrent requests
            max_pages=10  # Maximum number of pages to scrape
        )

if __name__ == "__main__":
    asyncio.run(main())
```

Run the script:
```bash
python run_scraper.py
```

### 2. As an API Service

The project can be run as a FastAPI service, providing RESTful endpoints to trigger and monitor scraping jobs.

#### Starting the API Server

1. Install additional dependencies:
```bash
pip install fastapi uvicorn
```

2. Run the API server:
```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

#### API Endpoints

##### Start a Scraping Job
```http
POST /jobs/scrape
Content-Type: application/json

{
    "url": "https://example.com/careers",
    "batch_size": 10,
    "max_concurrent": 5,
    "max_pages": 10
}
```

Response:
```json
{
    "task_id": "uuid-task-id"
}
```

##### Check Scraping Status
```http
GET /jobs/scrape/{task_id}/status
```

Response:
```json
{
    "task_id": "uuid-task-id",
    "status": "running",
    "progress": 45,
    "error": null,
    "started_at": "2024-03-14T12:00:00Z",
    "completed_at": null
}
```

##### Get All Scraped Jobs
```http
GET /jobs
```

Response:
```json
{
    "jobs": [
        {
            "id": "uuid",
            "job_url": "https://example.com/careers/job-1",
            "company_name": "Example Corp",
            "job_title": "Software Engineer",
            "location": "Remote",
            "created_at": "2024-03-14T12:00:00Z",
            "updated_at": "2024-03-14T12:00:00Z"
        }
    ]
}
```

## API Documentation

Once the server is running, you can access the interactive API documentation:

- Swagger UI: `http://localhost:8000/docs`
- ReDoc: `http://localhost:8000/redoc`
