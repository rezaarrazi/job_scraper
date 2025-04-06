import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
import json
from datetime import datetime
import time
from typing import List, Dict
import csv

# Load environment variables
load_dotenv()

class LinkedInJobScraper:
    def __init__(self, auth_file: str = None):
        """Initialize the scraper with optional authentication file."""
        self.auth_file = auth_file or os.path.join(
            os.path.dirname(__file__), 
            '.auth', 
            'linkedin_auth.json'
        )

    def ensure_authenticated(self, page) -> bool:
        """Check if we're authenticated, if not, perform login."""
        if page.url.startswith('https://www.linkedin.com/login'):
            # Need to login
            print("Logging in to LinkedIn...")
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
            print("Navigated to full jobs listing page")
            
        except Exception as e:
            print(f"Error clicking 'Show all jobs': {str(e)}")
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
            print(f"Error extracting job details: {str(e)}")
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
            print(f"Found scrollable container with class: {scrollable_container}")
            
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
    
    def scrape_company_jobs(self, company_url: str) -> List[Dict]:
        """
        Scrape all job listings from a company's LinkedIn jobs page.
        
        Args:
            company_url: URL of the company's LinkedIn jobs page
                       (e.g., 'https://www.linkedin.com/company/mekari/jobs/')
        
        Returns:
            List of dictionaries containing job details
        """
        jobs = []
        
        with sync_playwright() as p:
            # Launch browser with saved authentication if available
            browser = p.chromium.launch(headless=False)  # Set to True in production
            context = browser.new_context(
                storage_state=self.auth_file if os.path.exists(self.auth_file) else None
            )
            
            page = context.new_page()
            
            try:
                # Navigate to the company's jobs page
                print(f"Navigating to {company_url}")
                page.goto(company_url)
                
                # Check if we need to authenticate
                if self.ensure_authenticated(page):
                    # Save authentication state for future use
                    context.storage_state(path=self.auth_file)
                
                # Click "Show all jobs" button to get the full listing
                self.click_show_all_jobs(page)
                
                # Wait for job cards to load
                page.wait_for_selector('div.jobs-search-results-list__subtitle')

                results_count = page.locator('div.jobs-search-results-list__subtitle > span[dir="ltr"]').inner_text()
                print(f"Found {results_count} job listings")

                self.scroll_to_bottom(page)

                all_jobs = []
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
                
                print(f"Total pages to process: {total_pages}")

                while page_number <= total_pages:
                    print(f"\nProcessing page {page_number} of {total_pages}...")
                    
                    # Extract job links from current page
                    job_links = page.evaluate('''() => {
                        const links = Array.from(document.querySelectorAll('a.job-card-container__link'));
                        return links.map(link => {
                            // Extract job ID from URL
                            const url = link.href;
                            const jobIdMatch = url.match(/\/jobs\/view\/(\d+)\//);
                            const jobId = jobIdMatch ? jobIdMatch[1] : '';
                            
                            return {
                                title: link.querySelector('span strong')?.textContent.trim() || '',
                                url: link.href,
                                job_id: jobId
                            };
                        });
                    }''')

                    print(f"Found {len(job_links)} job listings on page {page_number}")
                    all_jobs.extend(job_links)

                    # Try to find and click the "Next" button
                    try:
                        if page_number < total_pages:
                            next_button = page.locator('button[aria-label="View next page"]')
                            print("Clicking next page...")
                            next_button.click()
                            page.wait_for_timeout(2000)  # Wait for the new page to load
                            self.scroll_to_bottom(page)
                        page_number += 1
                        
                    except Exception as e:
                        print(f"Error navigating to next page: {str(e)}")
                        break

                # Save to CSV file
                timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
                csv_file = f'./data/output/linkedin_jobs_{timestamp}.csv'
                
                with open(csv_file, 'w', encoding='utf-8', newline='') as f:
                    writer = csv.DictWriter(f, fieldnames=['job_id', 'title', 'url'])
                    writer.writeheader()
                    writer.writerows(all_jobs)
                
                print(f"\nTotal jobs found across all pages: {len(all_jobs)}")
                print(f"Saved job listings to {csv_file}")
                
            except Exception as e:
                print(f"Error during scraping: {str(e)}")
            
            finally:
                # # Add wait before closing
                # print("Waiting 10 seconds before closing browser...")
                # page.wait_for_timeout(10000)  # Wait for 10 seconds (time is in milliseconds)
                
                context.close()
                browser.close()
            
            return jobs

    def save_jobs_to_file(self, jobs: List[Dict], output_file: str = None):
        """Save scraped jobs to a JSON file."""
        if output_file is None:
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            output_file = f'linkedin_jobs_{timestamp}.json'
            
        with open(output_file, 'w', encoding='utf-8') as f:
            json.dump(jobs, f, indent=2, ensure_ascii=False)
            
        print(f"Saved {len(jobs)} jobs to {output_file}")

def main():
    # Example usage
    company_url = "https://www.linkedin.com/company/mekari/jobs/"
    scraper = LinkedInJobScraper()
    
    try:
        # Scrape jobs
        jobs = scraper.scrape_company_jobs(company_url)
        
        # # Save to file
        # scraper.save_jobs_to_file(jobs)
        
    except Exception as e:
        print(f"Error during scraping: {str(e)}")

if __name__ == "__main__":
    main() 