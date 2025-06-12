import os
import csv
from datetime import datetime
import json
from typing import List, Dict, Callable
from utils.logger import setup_logger
from functools import wraps
from dataclasses import dataclass
from tqdm import tqdm
from supabase import create_client, Client
from dotenv import load_dotenv

# Fieldnames for CSV writing
fieldnames = [
    'linkedin_job_url', 'job_id', 'job_title', 'company_name', 'location',
    'posted_date', 'is_active', 'work_arrangement', 'contract_type',
    'seniority_level', 'company_apply_url', 'description'
]

logger = setup_logger(__name__)

load_dotenv()

def create_supabase_client():
    """Create Supabase client with appropriate key from environment variables."""
    supabase_url = os.getenv("SUPABASE_URL")
    service_key = os.getenv("SUPABASE_SERVICE_ROLE_KEY")
    anon_key = os.getenv("SUPABASE_ANON_KEY")
    if service_key:
        return create_client(supabase_url, service_key)
    elif anon_key:
        return create_client(supabase_url, anon_key)
    else:
        raise ValueError("Either SUPABASE_SERVICE_ROLE_KEY or SUPABASE_ANON_KEY environment variable is required")

supabase: Client = create_supabase_client()

def job_id_exists_in_supabase(job_id: str) -> bool:
    """
    Check if a job_id already exists in the JobRaw table in Supabase.
    Returns True if exists, False otherwise.
    """
    try:
        response = supabase.table("JobRaw").select("jobId").eq("jobId", job_id).limit(1).execute()
        return bool(response.data)
    except Exception as e:
        logger.error(f"Error checking job_id {job_id} in Supabase: {str(e)}")
        return False

def retry_on_failure(max_retries: int = 3, delay: float = 2.0, backoff: float = 2.0):
    """
    Decorator to retry a function on failure with exponential backoff.
    Args:
        max_retries: Maximum number of retry attempts
        delay: Initial delay between retries in seconds
        backoff: Multiplier for delay after each retry
    """
    def decorator(func: Callable):
        @wraps(func)
        def wrapper(*args, **kwargs):
            last_exception = None
            current_delay = delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except Exception as e:
                    last_exception = e
                    if attempt == max_retries:
                        logger.error(f"Function {func.__name__} failed after {max_retries} retries. Last error: {str(e)}")
                        raise e
                    logger.warning(f"Function {func.__name__} failed on attempt {attempt + 1}/{max_retries + 1}. Retrying in {current_delay:.1f}s. Error: {str(e)}")
                    import time
                    time.sleep(current_delay)
                    current_delay *= backoff
            raise last_exception
        return wrapper
    return decorator

def safe_html_content(page, prefix: str = "page", html_dir: str = "job_pages"):
    """
    Save the HTML content of a page to a file.
    Args:
        page: Playwright page object
        prefix: Prefix for the filename
        html_dir: Directory to save HTML files
    """
    page_content = page.content()
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    os.makedirs(html_dir, exist_ok=True)
    filename = f'{html_dir}/{prefix}_{timestamp}.html'
    with open(filename, 'w', encoding='utf-8') as f:
        f.write(page_content)
    logger.info(f"Saved HTML content to: {filename}")

def save_jobs_to_file(all_jobs: List[Dict], organization_name: str, dir_prefix_date: str, fieldnames: List[str] = fieldnames):
    """
    Save scraped jobs to a CSV file.
    Args:
        all_jobs: List of job dictionaries
        organization_name: Name of the organization
        dir_prefix_date: Directory prefix (date string)
        fieldnames: List of CSV fieldnames
    """
    directory = f'./data/output/{dir_prefix_date}/{organization_name}'
    os.makedirs(directory, exist_ok=True)
    csv_file = f'{directory}/linkedin_jobs.csv'
    with open(csv_file, 'w', encoding='utf-8', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(all_jobs)
    logger.info(f"Saved {len(all_jobs)} jobs to {csv_file}")

def extract_job_cards_js() -> str:
    """
    Returns the JavaScript snippet for extracting job cards from a LinkedIn jobs page.
    This should be used with Playwright's page.evaluate().
    """
    return '''
        () => {
            const jobCards = document.querySelectorAll('div.job-card-container');
            const jobs = [];
            jobCards.forEach(card => {
                try {
                    const jobLink = card.querySelector('a.job-card-container__link');
                    if (!jobLink) return;
                    const href = jobLink.getAttribute('href');
                    if (!href || !href.includes('/jobs/view/')) return;
                    const jobInfo = { linkedin_job_url: href };
                    const jobIdMatch = href.match(/\/jobs\/view\/(\d+)\//);
                    if (jobIdMatch) {
                        jobInfo.job_id = jobIdMatch[1];
                    }
                    const titleElem = card.querySelector('a.job-card-container__link span[aria-hidden="true"] strong');
                    if (titleElem) {
                        jobInfo.job_title = titleElem.textContent.trim();
                    }
                    const companyElem = card.querySelector('div.artdeco-entity-lockup__subtitle span');
                    if (companyElem) {
                        jobInfo.company_name = companyElem.textContent.trim();
                    }
                    const locationElem = card.querySelector('ul.job-card-container__metadata-wrapper li span');
                    if (locationElem) {
                        jobInfo.location = locationElem.textContent.trim();
                    }
                    const postedDateElem = card.querySelector('li.job-card-container__footer-item time');
                    if (postedDateElem) {
                        const postedDate = postedDateElem.getAttribute('datetime');
                        if (postedDate) {
                            jobInfo.posted_date = postedDate;
                        }
                    }
                    jobInfo.posted_date = "";
                    if (Object.keys(jobInfo).length > 1) {
                        jobs.push(jobInfo);
                    }
                } catch (error) {
                    // Ignore extraction errors for individual cards
                }
            });
            return jobs;
        }
    '''

@dataclass
class LinkedInCredentials:
    username: str
    password: str
    auth_file: str

class LinkedInJobScraper:
    def __init__(self, credentials: LinkedInCredentials, worker_id: int = 0):
        """Initialize the scraper with credentials and worker ID."""
        self.credentials = credentials
        self.worker_id = worker_id
        self.auth_file = os.path.join(
            os.path.dirname(__file__),
            '.auth',
            credentials.auth_file
        )
        logger.info(f"[Worker {self.worker_id}] Using authentication file: {self.auth_file}")

    def safe_html_content(self, page):
        """Save the HTML content of a page to a file."""
        page_content = page.content()
        html_dir = 'job_pages'
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        os.makedirs(html_dir, exist_ok=True)
        with open(f'{html_dir}/page_{timestamp}.html', 'w', encoding='utf-8') as f:
            f.write(page_content)

    @retry_on_failure(max_retries=3, delay=2.0)
    def navigate_to_page(self, page, url: str):
        """Navigate to a page with retry logic."""
        logger.info(f"[Worker {self.worker_id}] Navigating to {url}")
        try:
            page.goto(url, timeout=30000)  # 30 second timeout
        except Exception as e:
            if "net::" in str(e) or "ERR_" in str(e) or "DNS" in str(e):
                logger.error(f"[Worker {self.worker_id}] Network error navigating to {url}: {str(e)}")
                raise e
            elif "Timeout" in str(e) and "30000ms" in str(e):
                logger.error(f"[Worker {self.worker_id}] Navigation timeout for {url}: {str(e)}")
                raise e
            else:
                logger.warning(f"[Worker {self.worker_id}] Navigation issue (continuing): {str(e)}")
        try:
            page.wait_for_load_state('networkidle', timeout=8000)
            logger.debug(f"[Worker {self.worker_id}] Page reached network idle state")
        except Exception as e:
            logger.debug(f"[Worker {self.worker_id}] Network idle timeout (this is often normal for modern web apps)")
            try:
                page.wait_for_load_state('domcontentloaded', timeout=5000)
                logger.debug(f"[Worker {self.worker_id}] DOM content loaded successfully")
                page.wait_for_timeout(2000)
            except Exception as e2:
                logger.warning(f"[Worker {self.worker_id}] DOM content load also failed: {str(e2)}")
                page.wait_for_timeout(3000)
        try:
            current_url = page.url
            if not current_url or "linkedin.com" not in current_url:
                raise Exception(f"Page did not load properly. Current URL: {current_url}")
        except Exception as e:
            logger.error(f"[Worker {self.worker_id}] Page verification failed: {str(e)}")
            raise e

    @retry_on_failure(max_retries=2, delay=1.0)
    def navigate_to_page_simple(self, page, url: str):
        """Simple navigation without strict network idle requirements."""
        logger.info(f"[Worker {self.worker_id}] Simple navigation to {url}")
        page.goto(url, timeout=30000)
        page.wait_for_load_state('domcontentloaded', timeout=10000)
        page.wait_for_timeout(3000)
        current_url = page.url
        if not current_url or "linkedin.com" not in current_url:
            raise Exception(f"Page did not load properly. Current URL: {current_url}")

    @retry_on_failure(max_retries=2, delay=3.0)
    def ensure_authenticated(self, page) -> bool:
        """Check if we're authenticated, if not, perform login."""
        if page.url.startswith('https://www.linkedin.com/login'):
            logger.info(f"[Worker {self.worker_id}] Logging in to LinkedIn with account: {self.credentials.username}")
            page.fill('#username', self.credentials.username)
            page.fill('#password', self.credentials.password)
            page.click('button[type="submit"]')
            try:
                page.wait_for_url('https://www.linkedin.com/feed/', timeout=30000)
                logger.info(f"[Worker {self.worker_id}] Successfully logged in")
                return True
            except Exception as e:
                logger.error(f"[Worker {self.worker_id}] Login failed or took too long: {str(e)}")
                raise e
        return False

    def click_show_all_jobs(self, page):
        """Click the 'Show all jobs' button and wait for the full listing."""
        try:
            show_all_selector = "text='Show all jobs'"
            page.wait_for_selector(show_all_selector, timeout=5000)
            page.click(show_all_selector)
            page.wait_for_selector('.jobs-search__job-details--wrapper')
            logger.info(f"[Worker {self.worker_id}] Navigated to full jobs listing page")
        except Exception as e:
            logger.error(f"[Worker {self.worker_id}] Error clicking 'Show all jobs': {str(e)}")
            raise e

    def scroll_to_bottom(self, page):
        """Scroll to the bottom of the page."""
        scrollable_container = page.evaluate('''() => {
            const listElement = document.querySelector('.scaffold-layout__list');
            if (!listElement) return null;
            const header = listElement.querySelector('header');
            if (!header) return null;
            const scrollableDiv = header.nextElementSibling;
            if (!scrollableDiv) return null;
            return scrollableDiv.className.split(' ')[0];
        }''')
        if scrollable_container:
            last_height = page.evaluate('''(selector) => {
                const element = document.querySelector(selector);
                return element ? element.scrollHeight : 0;
            }''', f'.{scrollable_container}')
            while True:
                page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    if (element) {
                        element.scrollBy(0, 500);
                    }
                }''', f'.{scrollable_container}')
                page.wait_for_timeout(1000)
                new_height = page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    return element ? element.scrollHeight : 0;
                }''', f'.{scrollable_container}')
                if new_height == last_height:
                    break
                last_height = new_height

    @retry_on_failure(max_retries=3, delay=1.0)
    def navigate_to_next_page(self, page):
        """Navigate to the next page of job listings with retry logic."""
        next_button = page.locator('button[aria-label="View next page"]')
        logger.info(f"[Worker {self.worker_id}] Clicking next page...")
        next_button.click()
        page.wait_for_timeout(2000)

    @retry_on_failure(max_retries=3, delay=2.0)
    def wait_for_job_listings(self, page):
        """Wait for job listings to load with retry logic."""
        page.wait_for_selector('div.jobs-search-results-list__subtitle', timeout=15000)
        page.wait_for_timeout(3000)

    @retry_on_failure(max_retries=2, delay=1.0)
    def get_results_count(self, page) -> str:
        """Get the results count with retry logic."""
        try:
            return page.locator('div.jobs-search-results-list__subtitle > span[dir="ltr"]').inner_text()
        except Exception as e:
            logger.warning(f"[Worker {self.worker_id}] Could not get results count: {str(e)}")
            return "Unknown"

    @retry_on_failure(max_retries=3, delay=1.0)
    def get_job_details(self, page, linkedin_job_url) -> Dict:
        """Get job details from the page."""
        details = {}
        job_url = f"https://www.linkedin.com{linkedin_job_url}"
        try:
            self.navigate_to_page(page, job_url)
        except Exception as e:
            logger.debug(f"[Worker {self.worker_id}] Standard navigation failed for job details, trying simple navigation: {str(e)}")
            self.navigate_to_page_simple(page, job_url)
        try:
            error_message = page.locator(".jobs-details-top-card__apply-error .artdeco-inline-feedback__message").text_content()
            if error_message and "No longer accepting applications" in error_message:
                details = {
                    "is_active": False,
                    "work_arrangement": None,
                    "contract_type": None,
                    "seniority_level": None,
                    "company_apply_url": "",
                    "description": ""
                }
                return details
        except Exception as e:
            logger.debug(f"[Worker {self.worker_id}] Error checking job status: {str(e)}")
        details["is_active"] = True
        try:
            first_li = page.locator("li.job-details-jobs-unified-top-card__job-insight.job-details-jobs-unified-top-card__job-insight--highlight").first
            label_spans = first_li.locator(":scope > span > span")
            values = [label_spans.nth(i).text_content().strip() for i in range(label_spans.count())]
            work_arrangement = values[0] if len(values) == 3 else None
            contract_type = values[1] if len(values) == 3 else values[0] if len(values) >= 1 else None
            seniority_level = values[2] if len(values) == 3 else values[1] if len(values) == 2 else None
            details["work_arrangement"] = work_arrangement
            details["contract_type"] = contract_type
            details["seniority_level"] = seniority_level
        except Exception as e:
            logger.error(f"[Worker {self.worker_id}] Error extracting job insights: {str(e)}")
        try:
            code_elements = page.locator("code")
            count = code_elements.count()
            data = {}
            for i in range(count):
                content = code_elements.nth(i).text_content()
                if content and "applyMethod" in content:
                    try:
                        json_data = json.loads(content).get("data", {})
                        if "applyMethod" in json_data:
                            data = json_data
                    except json.JSONDecodeError:
                        continue
            details["company_apply_url"] = data.get("applyMethod", {}).get("companyApplyUrl", "")
            details["description"] = data.get("description", {}).get("text", "")
        except Exception as e:
            logger.error(f"[Worker {self.worker_id}] Error extracting job data from code tag: {str(e)}")
        return details

    def scrape_company_jobs(self, company_url: str, organization_name: str, dir_prefix_date: str, headless: bool = True) -> List[Dict]:
        """
        Scrape all job listings from a company's LinkedIn jobs page.
        Args:
            company_url: URL of the company's LinkedIn jobs page
            organization_name: Name of the organization being scraped
        Returns:
            List of dictionaries containing job details
        """
        all_jobs = []
        from playwright.sync_api import sync_playwright
        import os
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=headless)
            context = browser.new_context(
                storage_state=self.auth_file if os.path.exists(self.auth_file) else None
            )
            page = context.new_page()
            page.set_default_timeout(15000)
            try:
                try:
                    self.navigate_to_page(page, company_url)
                except Exception as e:
                    logger.warning(f"[Worker {self.worker_id}] Standard navigation failed, trying simple navigation: {str(e)}")
                    self.navigate_to_page_simple(page, company_url)
                if self.ensure_authenticated(page):
                    context.storage_state(path=self.auth_file)
                empty_jobs_selector = '.org-jobs-empty-jobs-module'
                if page.locator(empty_jobs_selector).count() > 0:
                    logger.info(f"[Worker {self.worker_id}] No jobs available for this company")
                    return []
                self.click_show_all_jobs(page)
                self.wait_for_job_listings(page)
                results_count = self.get_results_count(page)
                logger.info(f"[Worker {self.worker_id}] Found {results_count} job listings")
                self.scroll_to_bottom(page)
                page_number = 1
                total_pages = page.evaluate('''() => {
                    const pageState = document.querySelector('.jobs-search-pagination__page-state');
                    if (pageState) {
                        const match = pageState.textContent.match(/Page \d+ of (\d+)/);
                        return match ? parseInt(match[1]) : 1;
                    }
                    return 1;
                }''')
                logger.info(f"[Worker {self.worker_id}] Total pages to process: {total_pages}")
                while page_number <= total_pages:
                    logger.info(f"[Worker {self.worker_id}] Processing page {page_number} of {total_pages}...")
                    page_jobs = self.extract_job_cards(page)
                    logger.info(f"[Worker {self.worker_id}] Found {len(page_jobs)} job listings on page {page_number}")
                    all_jobs.extend(page_jobs)
                    try:
                        self.scroll_to_bottom(page)
                        if page_number < total_pages:
                            self.navigate_to_next_page(page)
                        page_number += 1
                    except Exception as e:
                        logger.error(f"[Worker {self.worker_id}] Error navigating to next page: {str(e)}")
                        break
                for job in tqdm(all_jobs, desc=f"[Worker {self.worker_id}] Getting job details"):
                    job_id = job.get('job_id')
                    if not job_id:
                        logger.warning(f"[Worker {self.worker_id}] Job missing job_id, skipping.")
                        continue
                    if job_id_exists_in_supabase(job_id):
                        logger.info(f"[Worker {self.worker_id}] Skipping job_id {job_id} (already exists in Supabase)")
                        continue
                    try:
                        job_details = self.get_job_details(page, job['linkedin_job_url'])
                        job.update(job_details)
                    except Exception as e:
                        logger.error(f"[Worker {self.worker_id}] Failed to get details for job {job_id}: {str(e)}")
                        job.update({
                            "is_active": None,
                            "work_arrangement": None,
                            "contract_type": None,
                            "seniority_level": None,
                            "company_apply_url": "",
                            "description": ""
                        })
                save_jobs_to_file(all_jobs, organization_name, dir_prefix_date)
            except Exception as e:
                logger.error(f"[Worker {self.worker_id}] Error during scraping {company_url}: {str(e)}")
            finally:
                context.close()
                browser.close()
            return all_jobs

    def save_jobs_to_file(self, all_jobs: List[Dict], organization_name: str, dir_prefix_date: str):
        """Save scraped jobs to a JSON file."""
        save_jobs_to_file(all_jobs, organization_name, dir_prefix_date)

    @retry_on_failure(max_retries=2, delay=1.0)
    def extract_job_cards(self, page) -> List[Dict]:
        """Extract job information from job cards in the search results."""
        try:
            page.wait_for_selector('div.job-card-container', state='visible', timeout=10000)
            jobs_data = page.evaluate(extract_job_cards_js())
            return jobs_data
        except Exception as e:
            logger.error(f"[Worker {self.worker_id}] Error extracting job cards: {str(e)}")
            raise e
