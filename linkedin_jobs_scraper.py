import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from datetime import datetime
from typing import List, Dict, Tuple
import csv
from utils.logger import setup_logger
import re
import argparse
import json
from tqdm import tqdm
import time
import random
import multiprocessing
from itertools import cycle
import pandas as pd
from dataclasses import dataclass
from typing import Optional

# Load environment variables
load_dotenv()

# Setup logger
logger = setup_logger(__name__)

@dataclass
class LinkedInCredentials:
    username: str
    password: str
    auth_file: str

class LinkedInJobScraper:
    def __init__(self, credentials: LinkedInCredentials):
        """Initialize the scraper with credentials."""
        self.credentials = credentials
        self.auth_file = os.path.join(
            os.path.dirname(__file__), 
            '.auth', 
            credentials.auth_file
        )
        logger.info(f"Using authentication file: {self.auth_file}")

    def ensure_authenticated(self, page) -> bool:
        """Check if we're authenticated, if not, perform login."""
        if page.url.startswith('https://www.linkedin.com/login'):
            # Need to login
            logger.info(f"Logging in to LinkedIn with account: {self.credentials.username}")
            page.fill('#username', self.credentials.username)
            page.fill('#password', self.credentials.password)
            page.click('button[type="submit"]')
            
            # Wait for navigation after login
            page.wait_for_url('https://www.linkedin.com/feed/')
            return True
        return False

    def click_show_all_jobs(self, page):
        """Click the 'Show all jobs' button and wait for the full listing."""
        try:
            # Wait for the "Show all jobs" button and click it
            show_all_selector = "text='Show all jobs'"
            page.wait_for_selector(show_all_selector, timeout=5000)
            page.click(show_all_selector)
            
            # Wait for the job listings page to load with the split view
            page.wait_for_selector('.jobs-search__job-details--wrapper')
            logger.info("Navigated to full jobs listing page")
            
        except Exception as e:
            logger.error(f"Error clicking 'Show all jobs': {str(e)}")
            pass

    def scroll_to_bottom(self, page):
        """Scroll to the bottom of the page."""
        # First, get the dynamic class name of the scrollable container
        scrollable_container = page.evaluate('''() => {
            const listElement = document.querySelector('.scaffold-layout__list');
            if (!listElement) return null;
            
            // Find the div after the header that's a direct child of scaffold-layout__list
            const header = listElement.querySelector('header');
            if (!header) return null;
            
            // Get the next sibling after header which should be our scrollable div
            const scrollableDiv = header.nextElementSibling;
            if (!scrollableDiv) return null;
            
            return scrollableDiv.className.split(' ')[0]; // Get the first class name
        }''')

        if scrollable_container:
            # logger.info(f"Found scrollable container with class: {scrollable_container}")
            
            # Now use this class for scrolling, but pass the selector as a parameter
            # to avoid JavaScript string interpolation issues
            last_height = page.evaluate('''(selector) => {
                const element = document.querySelector(selector);
                return element ? element.scrollHeight : 0;
            }''', f'.{scrollable_container}')
            
            while True:
                # Scroll down in smaller increments
                page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    if (element) {
                        // Scroll down by 500 pixels each time
                        element.scrollBy(0, 500);
                    }
                }''', f'.{scrollable_container}')
                
                # Wait for possible new content to load
                page.wait_for_timeout(1000)  # Reduced wait time since we're scrolling less
                
                # Calculate new scroll height
                new_height = page.evaluate('''(selector) => {
                    const element = document.querySelector(selector);
                    return element ? element.scrollHeight : 0;
                }''', f'.{scrollable_container}')
                
                # Break the loop if no new content loaded (heights are the same)
                if new_height == last_height:
                    break
                    
                last_height = new_height

        # page.wait_for_timeout(3000)
    
    def get_job_details(self, page, linkedin_job_url) -> Dict:
        """Get job details from the page."""
        details = {}

        # open one of the linkedin_job_url and save the html content to a file
        page.goto(f"https://www.linkedin.com{linkedin_job_url}")

        #   Check if job is no longer accepting applications
        try:
            error_message = page.locator(".jobs-details-top-card__apply-error .artdeco-inline-feedback__message").text_content()
            if error_message and "No longer accepting applications" in error_message:
                details["is_active"] = False
                return details
        except Exception as e:
            logger.debug(f"Error checking job status: {str(e)}")
        
        details["is_active"] = True

        # Extract job insights
        try:
            # Get the first relevant <li>
            first_li = page.locator("li.job-details-jobs-unified-top-card__job-insight.job-details-jobs-unified-top-card__job-insight--highlight").first

            # Select spans that are two levels down: <li> > <span> > <span>
            label_spans = first_li.locator(":scope > span > span")

            values = [label_spans.nth(i).text_content().strip() for i in range(label_spans.count())]

            # Assign values safely
            work_arrangement = values[0] if len(values) == 3 else None
            contract_type = values[1] if len(values) == 3 else values[0] if len(values) >= 1 else None
            seniority_level = values[2] if len(values) == 3 else values[1] if len(values) == 2 else None
            
            details["work_arrangement"] = work_arrangement
            details["contract_type"] = contract_type
            details["seniority_level"] = seniority_level
            
        except Exception as e:
            logger.error(f"Error extracting job insights: {str(e)}")

        
        try:
            # Get all <code> elements
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
            logger.error(f"Error extracting job data from code tag: {str(e)}")
        
        return details
    
    def scrape_company_jobs(self, company_url: str, organization_name: str, dir_prefix_date: str, headless: bool = True) -> List[Dict]:
        """
        Scrape all job listings from a company's LinkedIn jobs page.
        
        Args:
            company_url: URL of the company's LinkedIn jobs page
                       (e.g., 'https://www.linkedin.com/company/mekari/jobs/')
            organization_name: Name of the organization being scraped
        
        Returns:
            List of dictionaries containing job details
        """
        all_jobs = []
        
        with sync_playwright() as p:
            # Launch browser with saved authentication if available and performance optimizations
            browser = p.chromium.launch(
                headless=headless,
                args=[
                    '--disable-blink-features=AutomationControlled',
                    '--disable-dev-shm-usage',
                    '--no-sandbox',
                    '--disable-setuid-sandbox',
                    '--disable-gpu',
                    '--disable-background-timer-throttling',
                    '--disable-backgrounding-occluded-windows',
                    '--disable-renderer-backgrounding'
                ]
            )
            context = browser.new_context(
                storage_state=self.auth_file if os.path.exists(self.auth_file) else None
            )
            
            page = context.new_page()
            
            # Set shorter timeouts for better performance
            page.set_default_timeout(15000)  # 15 seconds instead of default 30
            
            try:
                # Navigate to the company's jobs page
                logger.info(f"Navigating to {company_url}")
                page.goto(company_url)
                
                # Check if we need to authenticate
                if self.ensure_authenticated(page):
                    # Save authentication state for future use
                    context.storage_state(path=self.auth_file)
                
                # Check if there are any jobs available
                empty_jobs_selector = '.org-jobs-empty-jobs-module'
                if page.locator(empty_jobs_selector).count() > 0:
                    logger.info("No jobs available for this company")
                    return []
                
                # Click "Show all jobs" button to get the full listing
                self.click_show_all_jobs(page)
                
                # Wait for job cards to load
                page.wait_for_selector('div.jobs-search-results-list__subtitle')
                page.wait_for_timeout(3000)

                results_count = page.locator('div.jobs-search-results-list__subtitle > span[dir="ltr"]').inner_text()
                logger.info(f"Found {results_count} job listings")

                self.scroll_to_bottom(page)

                page_number = 1

                # Get total number of pages
                total_pages = page.evaluate('''() => {
                    const pageState = document.querySelector('.jobs-search-pagination__page-state');
                    if (pageState) {
                        const match = pageState.textContent.match(/Page \d+ of (\d+)/);
                        return match ? parseInt(match[1]) : 1;
                    }
                    return 1;
                }''')
                
                logger.info(f"Total pages to process: {total_pages}")

                while page_number <= total_pages:
                    logger.info(f"\nProcessing page {page_number} of {total_pages}...")

                    # # save the page html content to a file
                    # page_content = page.content()
                    # directory = f'./data/output/{dir_prefix_date}/{organization_name}/html'
                    # os.makedirs(directory, exist_ok=True)
                    # with open(f'{directory}/page_{page_number}.html', 'w', encoding='utf-8') as f:
                    #     f.write(page_content)
                    
                    # Extract job information from current page using the new method
                    page_jobs = self.extract_job_cards(page)
                    logger.info(f"Found {len(page_jobs)} job listings on page {page_number}")
                    all_jobs.extend(page_jobs)

                    # Try to find and click the "Next" button
                    try:
                        if page_number < total_pages:
                            next_button = page.locator('button[aria-label="View next page"]')
                            logger.info("Clicking next page...")
                            next_button.click()
                            page.wait_for_timeout(1000)  # Wait for the new page to load
                            self.scroll_to_bottom(page)
                        page_number += 1
                        
                    except Exception as e:
                        logger.error(f"Error navigating to next page: {str(e)}")
                        break
                
                # iterate all_jobs and get details from each job from linkedin_job_url
                for job in tqdm(all_jobs, desc="Getting job details"):
                    job_details = self.get_job_details(page, job['linkedin_job_url'])
                    job.update(job_details)

                # Save to CSV file
                self.save_jobs_to_file(all_jobs, organization_name, dir_prefix_date)
                
            except Exception as e:
                logger.error(f"Error during scraping: {str(e)}")
            
            finally:
                context.close()
                browser.close()
            
            return all_jobs

    def save_jobs_to_file(self, all_jobs: List[Dict], organization_name: str, dir_prefix_date: str):
        """Save scraped jobs to a JSON file."""
        directory = f'./data/output/{dir_prefix_date}/{organization_name}'
        os.makedirs(directory, exist_ok=True)
        
        csv_file = f'{directory}/linkedin_jobs.csv'
        
        with open(csv_file, 'w', encoding='utf-8', newline='') as f:
            fieldnames = list(all_jobs[0].keys())
            writer = csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(all_jobs)
            
        logger.info(f"Saved {len(all_jobs)} jobs to {csv_file}")

    def extract_job_cards(self, page) -> List[Dict]:
        """Extract job information from job cards in the search results."""
        jobs = []
        
        try:
            # Wait for job cards to be visible with reduced timeout
            page.wait_for_selector('div.job-card-container', state='visible', timeout=10000)
            
            # Extract all job data in a single JavaScript evaluation for better performance
            jobs_data = page.evaluate('''
                () => {
                    const jobCards = document.querySelectorAll('div.job-card-container');
                    const jobs = [];
                    
                    jobCards.forEach(card => {
                        try {
                            const jobLink = card.querySelector('a.job-card-container__link');
                            if (!jobLink) return;
                            
                            const href = jobLink.getAttribute('href');
                            if (!href || !href.includes('/jobs/view/')) return;
                            
                            const jobInfo = {
                                linkedin_job_url: href
                            };
                            
                            // Extract job ID from URL
                            const jobIdMatch = href.match(/\/jobs\/view\/(\d+)\//);
                            if (jobIdMatch) {
                                jobInfo.job_id = jobIdMatch[1];
                            }
                            
                            // Extract job title
                            const titleElem = card.querySelector('a.job-card-container__link span[aria-hidden="true"] strong');
                            if (titleElem) {
                                jobInfo.job_title = titleElem.textContent.trim();
                            }
                            
                            // Extract company name
                            const companyElem = card.querySelector('div.artdeco-entity-lockup__subtitle span');
                            if (companyElem) {
                                jobInfo.company_name = companyElem.textContent.trim();
                            }
                            
                            // Extract location
                            const locationElem = card.querySelector('ul.job-card-container__metadata-wrapper li span');
                            if (locationElem) {
                                jobInfo.location = locationElem.textContent.trim();
                            }
                            
                            // Extract posted date
                            const postedDateElem = card.querySelector('li.job-card-container__footer-item time');
                            if (postedDateElem) {
                                const postedDate = postedDateElem.getAttribute('datetime');
                                if (postedDate) {
                                    jobInfo.posted_date = postedDate;
                                }
                            }
                            
                            if (Object.keys(jobInfo).length > 1) { // More than just the URL
                                jobs.push(jobInfo);
                            }
                            
                        } catch (error) {
                            console.error('Error extracting job card:', error);
                        }
                    });
                    
                    return jobs;
                }
            ''')
            
            jobs = jobs_data
            
        except Exception as e:
            logger.error(f"Error extracting job cards: {str(e)}")
            
        return jobs

def process_companies_chunk(args: Tuple[List[Dict], List[LinkedInCredentials], str, bool, int]) -> None:
    """Process a chunk of companies using a specific account."""
    companies_chunk, credentials, dir_prefix_date, headless, worker_id = args
    
    # Initialize scraper with the assigned credentials
    scraper = LinkedInJobScraper(credentials)
    
    for idx, company in enumerate(companies_chunk):
        current = idx + 1
        total = len(companies_chunk)
        org_name = company['Organization Name']
        linkedin_url = company['LinkedIn']
        
        if pd.isna(linkedin_url) or not linkedin_url:
            logger.warning(f"[Worker {worker_id}] [{current}/{total}] Skipping {org_name} - no LinkedIn URL provided")
            continue
            
        # Ensure the URL ends with /jobs/
        base_url = linkedin_url.rstrip('/')
        linkedin_jobs_url = f"{base_url}/jobs/"
        
        logger.info(f"[Worker {worker_id}] [{current}/{total}] Processing {org_name} - {linkedin_jobs_url}")
        jobs = scraper.scrape_company_jobs(linkedin_jobs_url, org_name, dir_prefix_date, headless)
        
        if jobs:
            logger.info(f"[Worker {worker_id}] [{current}/{total}] Successfully scraped {len(jobs)} jobs from {org_name}")
        else:
            logger.warning(f"[Worker {worker_id}] [{current}/{total}] No jobs were scraped for {org_name}")
        
        # Add random delay between companies
        wait_time = random.randint(1, 5)
        logger.info(f"[Worker {worker_id}] Waiting {wait_time} seconds before next company...")
        time.sleep(wait_time)

def main():
    """Main function to run the LinkedIn job scraper."""
    parser = argparse.ArgumentParser(description='Scrape job listings from a company\'s LinkedIn page')
    parser.add_argument('--linkedin-url', 
                       help='URL of the company\'s LinkedIn jobs page (e.g., https://www.linkedin.com/company/mekari/jobs/)')
    parser.add_argument('--organization-name', 
                       help='Name of the organization to use in output files')
    parser.add_argument('--companies-data-file',
                       help='Path to CSV file containing company data with "Organization Name" and "LinkedIn" columns')
    parser.add_argument('--start-index',
                       type=int,
                       default=0,
                       help='Index to start processing from in the CSV file (default: 0)')
    parser.add_argument('--auth-file', 
                       default='linkedin_auth.json',
                       help='Path to the authentication state file (default: linkedin_auth.json)')
    parser.add_argument('--headless', 
                       action='store_true',
                       help='Run in headless mode')
    parser.add_argument('--num-workers',
                       type=int,
                       default=1,
                       help='Number of parallel workers to use (default: 1)')
    parser.add_argument('--accounts-file',
                       help='Path to JSON file containing LinkedIn account credentials')
    
    args = parser.parse_args()
    
    dir_prefix_date = datetime.now().strftime('%Y%m%d_%H%M%S')
    
    if args.companies_data_file:
        # Process companies from CSV file
        try:
            companies_df = pd.read_csv(args.companies_data_file)
            required_columns = ['Organization Name', 'LinkedIn']
            
            if not all(col in companies_df.columns for col in required_columns):
                logger.error(f"CSV file must contain columns: {required_columns}")
                return
                
            total_companies = len(companies_df)
            if args.start_index >= total_companies:
                logger.error(f"Start index {args.start_index} is out of range. File has {total_companies} companies.")
                return

            # Load LinkedIn accounts
            if not args.accounts_file:
                logger.error("--accounts-file is required for parallel processing")
                return

            with open(args.accounts_file, 'r') as f:
                accounts_data = json.load(f)
            
            credentials_list = [
                LinkedInCredentials(
                    username=account['username'],
                    password=account['password'],
                    auth_file=account['auth_file']
                )
                for i, account in enumerate(accounts_data)
            ]

            if not credentials_list:
                logger.error("No valid LinkedIn accounts found in the accounts file")
                return

            # Determine number of workers (can't exceed number of accounts)
            num_workers = min(args.num_workers, len(credentials_list))
            logger.info(f"Using {num_workers} workers with {len(credentials_list)} accounts")

            # Split companies into chunks for each worker
            companies_list = companies_df.iloc[args.start_index:].to_dict('records')
            chunk_size = len(companies_list) // num_workers
            if len(companies_list) % num_workers:
                chunk_size += 1
            
            company_chunks = [
                companies_list[i:i + chunk_size]
                for i in range(0, len(companies_list), chunk_size)
            ]

            # Create worker arguments
            worker_args = [
                (chunk, credentials_list[i % len(credentials_list)], dir_prefix_date, args.headless, i)
                for i, chunk in enumerate(company_chunks)
            ]

            # Process companies in parallel
            with multiprocessing.Pool(num_workers) as pool:
                pool.map(process_companies_chunk, worker_args)

        except Exception as e:
            logger.error(f"Error processing companies data file: {str(e)}")
            
    elif args.linkedin_url and args.organization_name:
        # Process single company (non-parallel mode)
        credentials = LinkedInCredentials(
            username=os.getenv('LINKEDIN_USERNAME'),
            password=os.getenv('LINKEDIN_PASSWORD'),
            auth_file=args.auth_file
        )
        scraper = LinkedInJobScraper(credentials)
        
        base_url = args.linkedin_url.rstrip('/')
        linkedin_jobs_url = f"{base_url}/jobs/"
        jobs = scraper.scrape_company_jobs(linkedin_jobs_url, args.organization_name, dir_prefix_date, args.headless)
        
        if jobs:
            logger.info(f"Successfully scraped {len(jobs)} jobs from {args.organization_name}")
        else:
            logger.error("No jobs were scraped")
    else:
        logger.error("Either provide both --linkedin-url and --organization-name, or --companies-data-file")

if __name__ == "__main__":
    main() 