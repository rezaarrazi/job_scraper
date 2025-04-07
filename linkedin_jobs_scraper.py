import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from datetime import datetime
from typing import List, Dict
import csv
from utils.logger import setup_logger
import re
import argparse
import json
from tqdm import tqdm
# Load environment variables
load_dotenv()

# Setup logger
logger = setup_logger(__name__)

class LinkedInJobScraper:
    def __init__(self, auth_file: str = None):
        """Initialize the scraper with optional authentication file."""
        if auth_file:
            auth_file = 'linkedin_auth.json'

        self.auth_file = os.path.join(
            os.path.dirname(__file__), 
            '.auth', 
            auth_file
        )

    def ensure_authenticated(self, page) -> bool:
        """Check if we're authenticated, if not, perform login."""
        if page.url.startswith('https://www.linkedin.com/login'):
            # Need to login
            logger.info("Logging in to LinkedIn...")
            page.fill('#username', os.getenv('LINKEDIN_USERNAME'))
            page.fill('#password', os.getenv('LINKEDIN_PASSWORD'))
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

    def extract_job_details(self, page) -> Dict:
        """Extract details from the job details panel."""
        details = {}
        
        try:
            # Wait for the job details wrapper to be visible
            details_wrapper = '.jobs-search__job-details--wrapper'
            page.wait_for_selector(details_wrapper, state='visible')
            
            # Wait for the content to load in the details panel
            page.wait_for_selector('.jobs-unified-top-card__job-title', state='visible')
            
            # Extract basic information
            details['title'] = page.locator('.jobs-unified-top-card__job-title').inner_text()
            details['company'] = page.locator('.jobs-unified-top-card__company-name').inner_text()
            details['location'] = page.locator('.jobs-unified-top-card__bullet').inner_text()
            
            # Extract job description
            description_selector = '.jobs-description__content'
            page.wait_for_selector(description_selector)
            details['description'] = page.locator(description_selector).inner_text()
            
            # Extract posting date
            try:
                details['posted_date'] = page.locator('.jobs-unified-top-card__posted-date').inner_text()
            except:
                details['posted_date'] = None
            
            # Extract employment type and other metadata
            try:
                metadata_items = page.locator('.jobs-unified-top-card__job-insight').all()
                details['metadata'] = [item.inner_text() for item in metadata_items]
            except:
                details['metadata'] = []
            
            # Extract skills if available
            try:
                skills_selector = '.jobs-description__content .description__skills'
                if page.locator(skills_selector).count() > 0:
                    details['skills'] = page.locator(skills_selector).inner_text()
            except:
                details['skills'] = None
                
        except Exception as e:
            logger.error(f"Error extracting job details: {str(e)}")
            return None
            
        return details

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
            logger.info(f"Found scrollable container with class: {scrollable_container}")
            
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

        page.wait_for_timeout(3000)
    
    def get_job_details(self, page, linkedin_job_url) -> Dict:
        """Get job details from the page."""
        details = {}

        # open one of the linkedin_job_url and save the html content to a file
        page.goto(f"https://www.linkedin.com{linkedin_job_url}")
        
        # Click the "See more" button to show full description
        try:
            see_more_button = page.locator('button[aria-label="Click to see more description"]')
            if see_more_button.count() > 0:
                see_more_button.click()
                page.wait_for_timeout(2000)  # Wait for description to expand
        except Exception as e:
            logger.error(f"Error clicking 'See more' button: {str(e)}")

        # Extract job insights
        try:
            # Get all job insight items
            job_insights = page.locator('.job-details-jobs-unified-top-card__job-insight').all()
            insights = []
            
            for insight in job_insights[:3]:
                # Get all spans within the insight that contain the actual text
                spans = insight.locator('span[dir="ltr"]').all()
                for span in spans:
                    text = span.inner_text().strip()
                    if text:  # Only add non-empty text
                        insights.append(text)
            
            details["work_arrangement"] = insights[0]
            details["contract_type"] = insights[1]
            details["seniority_level"] = insights[2]
            
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
    
    def scrape_company_jobs(self, company_url: str, organization_name: str, headless: bool = True) -> List[Dict]:
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
            # Launch browser with saved authentication if available
            browser = p.chromium.launch(headless=headless)  # Set to True in production
            context = browser.new_context(
                storage_state=self.auth_file if os.path.exists(self.auth_file) else None
            )
            
            page = context.new_page()
            
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

                dir_prefix_date = datetime.now().strftime('%Y%m%d_%H%M%S')
                total_pages = 1

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
                            page.wait_for_timeout(3000)  # Wait for the new page to load
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
            # Wait for job cards to be visible
            page.wait_for_selector('div.job-card-container', state='visible')
            
            # Get all job cards
            job_cards = page.locator('div.job-card-container').all()
            
            for card in job_cards:
                job_info = {}
                
                try:
                    # Extract job link and ID
                    job_link = card.locator('a.job-card-container__link').first
                    if job_link:
                        href = job_link.get_attribute('href')

                        if href:
                            if '/jobs/view/' not in href:
                                continue    
                            
                            job_info['linkedin_job_url'] = href
                            # Extract job ID from URL
                            job_id_match = re.search(r'/jobs/view/(\d+)/', href)
                            if job_id_match:
                                job_info['job_id'] = job_id_match.group(1)
                    
                            # Extract job title
                            title_elem = card.locator('a.job-card-container__link span[aria-hidden="true"] strong').first
                            if title_elem:
                                job_info['job_title'] = title_elem.inner_text().strip()
                            
                            # Extract company name - look for the subtitle span
                            company_elem = card.locator('div.artdeco-entity-lockup__subtitle span').first
                            if company_elem:
                                job_info['company_name'] = company_elem.inner_text().strip()
                            
                            # Extract location - look for the metadata list item
                            location_elem = card.locator('ul.job-card-container__metadata-wrapper li span').first
                            if location_elem:
                                job_info['location'] = location_elem.inner_text().strip()
                            
                            if job_info:  # Only add if we got at least some information
                                jobs.append(job_info)
                        
                except Exception as e:
                    logger.error(f"Error extracting job card details: {str(e)}")
                    continue
                    
        except Exception as e:
            logger.error(f"Error extracting job cards: {str(e)}")
            
        return jobs

def main():
    """Main function to run the LinkedIn job scraper."""
    parser = argparse.ArgumentParser(description='Scrape job listings from a company\'s LinkedIn page')
    parser.add_argument('linkedin_url', 
                       help='URL of the company\'s LinkedIn jobs page (e.g., https://www.linkedin.com/company/mekari/jobs/)')
    parser.add_argument('organization_name', 
                       help='Name of the organization to use in output files')
    parser.add_argument('--auth-file', 
                       default='linkedin_auth.json',
                       help='Path to the authentication state file (default: linkedin_auth.json)')
    parser.add_argument('--headless', 
                       action='store_true',
                       help='Run in headless mode')
    
    args = parser.parse_args()
    
    # Initialize the scraper
    scraper = LinkedInJobScraper(auth_file=args.auth_file)
    
    # Scrape jobs
    # Ensure the URL ends with /jobs/ by removing any trailing slash first
    base_url = args.linkedin_url.rstrip('/')
    linkedin_jobs_url = f"{base_url}/jobs/"
    jobs = scraper.scrape_company_jobs(linkedin_jobs_url, args.organization_name, args.headless)
    
    if jobs:
        logger.info(f"Successfully scraped {len(jobs)} jobs from {args.organization_name}")
    else:
        logger.error("No jobs were scraped")

if __name__ == "__main__":
    main() 