# test_get_job_details.py

import os
from playwright.sync_api import sync_playwright, TimeoutError
from utils.job_scraper import LinkedInJobScraper, LinkedInCredentials, save_html_content
from datetime import datetime
import time
import logging
import random

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def click_and_extract_job_details(page):
    job_cards = page.query_selector_all('div.job-card-container')
    logger.info(f"Found {len(job_cards)} job cards to process.")
    if not job_cards:
        logger.warning("No job cards found.")
        return
    # Prime the side panel by clicking the first card
    job_cards[0].click()
    page.wait_for_selector('div.job-details-jobs-unified-top-card__job-title h1', timeout=5000)
    time.sleep(1)
    last_title = None
    for i, card in enumerate(job_cards):
        try:
            card.click()
            # Wait for the job title in the side panel to change
            job_title = None
            for _ in range(10):  # Try for up to ~5 seconds
                page.wait_for_timeout(0.5 * 1000)
                title_elem = page.query_selector('div.job-details-jobs-unified-top-card__job-title h1')
                job_title = title_elem.inner_text().strip() if title_elem else ""
                if job_title and job_title != last_title:
                    break
            else:
                logger.warning(f"Job title did not update for card {i+1}")
            last_title = job_title

            # Location and posted date
            location_spans = page.query_selector_all('div.job-details-jobs-unified-top-card__primary-description-container span.tvm__text--low-emphasis')
            job_location = location_spans[0].inner_text().strip() if len(location_spans) > 0 else ""
            posted_date = location_spans[2].inner_text().strip() if len(location_spans) > 2 else ""

            # Work arrangement, contract type, seniority level (new logic)
            # Try new structure first
            insight_spans = page.query_selector_all('span[dir="ltr"], span[dir="ltr"].job-details-jobs-unified-top-card__job-insight-view-model-secondary')
            insight_texts = [span.inner_text().strip() for span in insight_spans if span.inner_text().strip()]
            if len(insight_texts) >= 3:
                work_arrangement = insight_texts[0]
                contract_type = insight_texts[1]
                seniority_level = insight_texts[2]
            else:
                # Fallback to old pill-based logic
                pills = page.query_selector_all('button.job-details-preferences-and-skills div.job-details-preferences-and-skills__pill span.ui-label.text-body-small')
                pill_texts = [pill.inner_text().strip() for pill in pills]
                work_arrangement = pill_texts[0] if len(pill_texts) > 0 else ""
                contract_type = pill_texts[1] if len(pill_texts) > 1 else ""
                seniority_level = pill_texts[2] if len(pill_texts) > 2 else ""

            # Job description
            job_description_elem = page.query_selector('div#job-details')
            job_description = job_description_elem.inner_text().strip() if job_description_elem else ""

            logger.info(f"[{i+1}/{len(job_cards)}] Title: {job_title} | Location: {job_location} | Posted: {posted_date} | Work: {work_arrangement} | Contract: {contract_type} | Seniority: {seniority_level}")
            logger.info(f"Description: {job_description[:100]}...")  # Print first 100 chars

            time.sleep(random.uniform(1, 2.5))  # Random delay
        except Exception as e:
            logger.error(f"Error processing job card {i+1}: {str(e)}")

def main():
    # Replace with a real LinkedIn job path (e.g., '/jobs/view/1234567890/')
    company_url = 'https://www.linkedin.com/company/mekari/jobs/'

    credentials = LinkedInCredentials(
            username=os.getenv('LINKEDIN_USERNAME'),
            password=os.getenv('LINKEDIN_PASSWORD'),
            auth_file=os.path.join(os.path.dirname(__file__), '.auth', 'linkedin_auth_arsy.json')
        )
    scraper = LinkedInJobScraper(credentials)  # Adjust if needed
    
    all_jobs = []
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Set to True in production
        context = browser.new_context(storage_state=scraper.auth_file)
        
        if context:
            page = context.new_page()
            try:
                scraper.navigate_to_page(page, company_url)
            except Exception as e:
                logger.warning(f"[Worker {scraper.worker_id}] Standard navigation failed, trying simple navigation: {str(e)}")
                scraper.navigate_to_page_simple(page, company_url)
            if scraper.ensure_authenticated(page):
                context.storage_state(path=scraper.auth_file)
            empty_jobs_selector = '.org-jobs-empty-jobs-module'
            if page.locator(empty_jobs_selector).count() > 0:
                logger.info(f"[Worker {scraper.worker_id}] No jobs available for this company")
                return []
            scraper.click_show_all_jobs(page)
            scraper.wait_for_job_listings(page)

            # Wait for spinner to disappear
            try:
                logger.info("Waiting for spinner to disappear")
                page.wait_for_selector('div.artdeco-loader', state='hidden', timeout=15000)
            except Exception:
                pass

            results_count = scraper.get_results_count(page)
            logger.info(f"[Worker {scraper.worker_id}] Found {results_count} job listings")
            scraper.scroll_to_bottom(page)

            # New: Click each job card and extract details from side panel
            click_and_extract_job_details(page)

            context.close() 

if __name__ == "__main__":
    main()