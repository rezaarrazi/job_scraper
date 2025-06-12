import os
from utils.job_scraper import LinkedInJobScraper, LinkedInCredentials
import pandas as pd
import argparse
import json
from datetime import datetime
from utils.logger import setup_logger
import random
import time
import multiprocessing
from tqdm import tqdm

logger = setup_logger(__name__)

def process_companies_chunk(args):
    companies_chunk, credentials, dir_prefix_date, headless, worker_id = args
    scraper = LinkedInJobScraper(credentials, worker_id)
    for idx, company in enumerate(companies_chunk):
        current = idx + 1
        total = len(companies_chunk)
        org_name = company['Organization Name']
        linkedin_url = company['LinkedIn']
        if pd.isna(linkedin_url) or not linkedin_url:
            logger.warning(f"[Worker {worker_id}] [{current}/{total}] Skipping {org_name} - no LinkedIn URL provided")
            continue
        base_url = linkedin_url.rstrip('/')
        linkedin_jobs_url = f"{base_url}/jobs/"
        logger.info(f"[Worker {worker_id}] [{current}/{total}] Processing {org_name} - {linkedin_jobs_url}")
        max_company_retries = 2
        for attempt in range(max_company_retries + 1):
            try:
                jobs = scraper.scrape_company_jobs(linkedin_jobs_url, org_name, dir_prefix_date, headless)
                if jobs:
                    logger.info(f"[Worker {worker_id}] [{current}/{total}] Successfully scraped {len(jobs)} jobs from {org_name}")
                else:
                    logger.warning(f"[Worker {worker_id}] [{current}/{total}] No jobs were scraped for {org_name}")
                break
            except Exception as e:
                if attempt == max_company_retries:
                    logger.error(f"[Worker {worker_id}] [{current}/{total}] Failed to scrape {org_name} after {max_company_retries + 1} attempts: {str(e)}")
                else:
                    wait_time = (attempt + 1) * 3
                    logger.warning(f"[Worker {worker_id}] [{current}/{total}] Attempt {attempt + 1} failed for {org_name}: {str(e)}. Retrying in {wait_time}s...")
                    time.sleep(wait_time)
        wait_time = random.randint(1, 5)
        logger.info(f"[Worker {worker_id}] Waiting {wait_time} seconds before next company...")
        time.sleep(wait_time)

def main():
    parser = argparse.ArgumentParser(description='Scrape job listings from a company\'s LinkedIn page')
    parser.add_argument('--linkedin-url', help='URL of the company\'s LinkedIn jobs page (e.g., https://www.linkedin.com/company/mekari/jobs/)')
    parser.add_argument('--organization-name', help='Name of the organization to use in output files')
    parser.add_argument('--companies-data-file', help='Path to CSV file containing company data with "Organization Name" and "LinkedIn" columns')
    parser.add_argument('--start-index', type=int, default=0, help='Index to start processing from in the CSV file (default: 0)')
    parser.add_argument('--end-index', type=int, default=0, help='Index to end processing in the CSV file (default: 0)')
    parser.add_argument('--auth-file', default='linkedin_auth.json', help='Path to the authentication state file (default: linkedin_auth.json)')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    parser.add_argument('--num-workers', type=int, default=1, help='Number of parallel workers to use (default: 1)')
    parser.add_argument('--accounts-file', help='Path to JSON file containing LinkedIn account credentials')
    args = parser.parse_args()
    dir_prefix_date = datetime.now().strftime('%Y%m%d_%H%M%S')
    if args.companies_data_file:
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
            if args.end_index == 0:
                args.end_index = total_companies
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
                for account in accounts_data
            ]
            if not credentials_list:
                logger.error("No valid LinkedIn accounts found in the accounts file")
                return
            num_workers = min(args.num_workers, len(credentials_list))
            logger.info(f"Using {num_workers} workers with {len(credentials_list)} accounts")
            companies_list = companies_df.iloc[args.start_index:args.end_index].to_dict(orient='records')
            chunk_size = len(companies_list) // num_workers
            if len(companies_list) % num_workers:
                chunk_size += 1
            company_chunks = [
                companies_list[i:i + chunk_size]
                for i in range(0, len(companies_list), chunk_size)
            ]
            worker_args = [
                (chunk, credentials_list[i % len(credentials_list)], dir_prefix_date, args.headless, i)
                for i, chunk in enumerate(company_chunks)
            ]
            with multiprocessing.Pool(num_workers) as pool:
                pool.map(process_companies_chunk, worker_args)
        except Exception as e:
            logger.error(f"Error processing companies data file: {str(e)}")
    elif args.linkedin_url and args.organization_name:
        credentials = LinkedInCredentials(
            username=os.getenv('LINKEDIN_USERNAME'),
            password=os.getenv('LINKEDIN_PASSWORD'),
            auth_file=args.auth_file
        )
        scraper = LinkedInJobScraper(credentials, worker_id=0)
        base_url = args.linkedin_url.rstrip('/')
        linkedin_jobs_url = f"{base_url}/jobs/"
        jobs = scraper.scrape_company_jobs(linkedin_jobs_url, args.organization_name, dir_prefix_date, args.headless)
        if jobs:
            logger.info(f"[Worker 0] Successfully scraped {len(jobs)} jobs from {args.organization_name}")
        else:
            logger.error("[Worker 0] No jobs were scraped")
    else:
        logger.error("Either provide both --linkedin-url and --organization-name, or --companies-data-file")

if __name__ == "__main__":
    main() 