# test_get_job_details.py

import os
from playwright.sync_api import sync_playwright, TimeoutError
from linkedin_jobs_scraper import LinkedInJobScraper, LinkedInCredentials
from datetime import datetime
import time
import logging

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def main():
    # Replace with a real LinkedIn job path (e.g., '/jobs/view/1234567890/')
    linkedin_job_url = 'https://www.linkedin.com/jobs/view/4206292770/?alternateChannel=search&refId=MbMpFeiTFI%2FxV8eizFiqfg%3D%3D&trackingId=pOv0Ka71MVaRX6hHJUwkwQ%3D%3D'

    credentials = LinkedInCredentials(
            username=os.getenv('LINKEDIN_USERNAME'),
            password=os.getenv('LINKEDIN_PASSWORD'),
            auth_file='linkedin_auth_arsy.json'
        )
    scraper = LinkedInJobScraper(credentials)  # Adjust if needed

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Set headless=True for no UI
        context = browser.new_context(
                storage_state=scraper.auth_file if os.path.exists(scraper.auth_file) else None
            )
            
        page = context.new_page()
        
        try:
            if scraper.ensure_authenticated(page):
                # Save authentication state for future use
                context.storage_state(path=scraper.auth_file)

            # Extract just the path part of the URL for the scraper
            job_path = linkedin_job_url.split('linkedin.com')[1]
            logger.info(f"Navigating to job path: {job_path}")

            # Navigate to the job page with a shorter timeout
            page.goto(linkedin_job_url, timeout=30000)
            
            # Wait for the main job content to be visible
            # LinkedIn job pages typically have these elements
            try:
                page.wait_for_selector('.job-view-layout', timeout=10000)
            except TimeoutError:
                logger.warning("Warning: Job content selector not found, but continuing...")

            # Give a small delay to ensure dynamic content loads
            time.sleep(2)

            # Create a directory for HTML content if it doesn't exist
            html_dir = 'job_pages'
            os.makedirs(html_dir, exist_ok=True)

            # Generate filename with timestamp
            timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
            job_id = linkedin_job_url.split('/jobs/view/')[1].split('/')[0]
            filename = f'{html_dir}/job_{job_id}_{timestamp}.html'

            # Save the HTML content
            html_content = page.content()
            with open(filename, 'w', encoding='utf-8') as f:
                f.write(html_content)
            logger.info(f"Saved HTML content to: {filename}")

            # Get job details using just the path part of the URL
            details = scraper.get_job_details(page, job_path)
            logger.info("Job Details: %s", details)

        except Exception as e:
            logger.error(f"An error occurred: {str(e)}")
        finally:
            browser.close()

if __name__ == "__main__":
    main()