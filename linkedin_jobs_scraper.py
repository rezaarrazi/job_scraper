import os
from utils.job_scraper import LinkedInJobScraper, LinkedInCredentials, create_supabase_client
import pandas as pd
import argparse
import json
from datetime import datetime
from utils.logger import setup_logger
import random
import time
import multiprocessing
from tqdm import tqdm
from cuid import cuid

logger = setup_logger(__name__)

def process_companies_chunk(args):
    companies_chunk, credentials, dir_prefix_date, headless, worker_id = args
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
    supabase_client = create_supabase_client(supabase_url, supabase_key)
    scraper = LinkedInJobScraper(credentials, worker_id, supabase_client)
    for idx, company in enumerate(companies_chunk):
        current = idx + 1
        total = len(companies_chunk)
        org_name = company['organizationName']
        linkedin_url = company['linkedin']
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
                    handle_job_db_operations(supabase_client, jobs, org_name, dir_prefix_date, company['id'])
                else:
                    logger.warning(f"[Worker {worker_id}] [{current}/{total}] No jobs were scraped for {org_name}")
                
                # Set isScrapedToday to True after processing
                try:
                    supabase_client.table("Company").update({"isScrapedToday": True}).eq("id", company['id']).execute()
                    logger.info(f"Set isScrapedToday=True for company {company['id']}")
                except Exception as e:
                    logger.error(f"Failed to update isScrapedToday for company {company['id']}: {str(e)}")
                
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

def handle_job_db_operations(supabase_client, jobs, organization_name, dir_prefix_date, company_id):
    """
    Handles filtering, inserting, and archiving jobs in Supabase for a given company.
    - Filters out jobs that already exist in JobRaw (by jobId and companyName)
    - Inserts new jobs (with companyId and id)
    - Archives jobs in JobRaw for this company that are not in the new jobs
    - Saves new jobs to file
    """
    from utils.job_scraper import save_jobs_to_file
    global logger
    # Fetch all existing jobIds for this company from Supabase
    try:
        response = supabase_client.table("JobRaw").select("jobId").eq("companyName", organization_name).execute()
        existing_job_ids = set(str(row["jobId"]) for row in response.data if row.get("jobId"))
    except Exception as e:
        logger.error(f"Error fetching jobIds for company {organization_name} from Supabase: {str(e)}")
        existing_job_ids = set()
    scraped_job_ids = set()
    new_jobs = []
    for job in jobs:
        job_id = job.get('jobId')
        if not job_id:
            logger.warning(f"Job missing jobId, skipping.")
            continue
        if job_id in existing_job_ids:
            logger.info(f"Skipping jobId {job_id} (already exists in Supabase)")
            scraped_job_ids.add(job_id)
            continue
        scraped_job_ids.add(job_id)
        job['companyId'] = company_id
        job['id'] = cuid()  # Use cuid for unique id
        new_jobs.append(job)
    # Insert new jobs into Supabase (batch)
    if new_jobs:
        try:
            supabase_client.table("JobRaw").insert(new_jobs).execute()
            logger.info(f"Inserted {len(new_jobs)} new jobs for company {organization_name}")
        except Exception as e:
            logger.error(f"Failed to batch insert new jobs: {str(e)}")
        # Save all jobs to file
        save_jobs_to_file(new_jobs, organization_name, dir_prefix_date)
    # Archive jobs that are no longer present
    to_archive = existing_job_ids - scraped_job_ids
    if to_archive:
        from datetime import datetime as dt
        now_str = dt.utcnow().isoformat()
        for job_id in to_archive:
            try:
                supabase_client.table("JobRaw").update({
                    "isArchived": True,
                    "archivedDate": now_str
                }).eq("jobId", job_id).eq("companyName", organization_name).execute()
                logger.info(f"Archived jobId {job_id} for company {organization_name}")
            except Exception as e:
                logger.error(f"Failed to archive jobId {job_id}: {str(e)}")

def main():
    parser = argparse.ArgumentParser(description='Scrape job listings from a company\'s LinkedIn page')
    parser.add_argument('--linkedin-url', help='URL of the company\'s LinkedIn jobs page (e.g., https://www.linkedin.com/company/mekari/jobs/)')
    parser.add_argument('--organization-name', help='Name of the organization to use in output files')
    parser.add_argument('--company-id', help='ID of the company in Supabase (required for individual company mode)')
    parser.add_argument('--from-db', action='store_true', help='Fetch companies from Supabase instead of a CSV file')
    parser.add_argument('--start-index', type=int, default=0, help='Index to start processing from in the company list (default: 0)')
    parser.add_argument('--end-index', type=int, default=0, help='Index to end processing in the company list (default: 0)')
    parser.add_argument('--auth-file', default='linkedin_auth.json', help='Path to the authentication state file (default: linkedin_auth.json)')
    parser.add_argument('--headless', action='store_true', help='Run in headless mode')
    parser.add_argument('--num-workers', type=int, default=1, help='Number of parallel workers to use (default: 1)')
    parser.add_argument('--accounts-file', help='Path to JSON file containing LinkedIn account credentials')
    args = parser.parse_args()
    dir_prefix_date = datetime.now().strftime('%Y%m%d_%H%M%S')
    supabase_url = os.getenv('SUPABASE_URL')
    supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
    if args.from_db:
        try:
            supabase_client = create_supabase_client(supabase_url, supabase_key)
            all_companies = []
            page_size = 1000
            page = 0
            while True:
                start = page * page_size
                # Use the get_companies_for_scraping RPC instead of direct table query
                response = supabase_client.rpc('get_companies_for_scraping', {'batch': page_size, 'offset_value': start}).execute()
                data = response.data
                if not data:
                    break
                all_companies.extend(data)
                if len(data) < page_size:
                    break
                page += 1
            required_columns = ['organizationName', 'linkedin']
            if not all_companies or not all(col in all_companies[0] for col in required_columns):
                logger.error(f"Supabase Company table must contain columns: {required_columns}")
                return
            total_companies = len(all_companies)
            logger.info(f"Found {total_companies} companies to process")
            
            if args.start_index >= total_companies:
                logger.error(f"Start index {args.start_index} is out of range. Table has {total_companies} companies.")
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
            companies_list = all_companies[args.start_index:args.end_index]
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
            logger.error(f"Error processing companies from Supabase: {str(e)}")
    elif args.linkedin_url and args.organization_name:
        if not args.company_id:
            logger.error("--company-id is required when using --linkedin-url and --organization-name")
            return
        credentials = LinkedInCredentials(
            username=os.getenv('LINKEDIN_USERNAME'),
            password=os.getenv('LINKEDIN_PASSWORD'),
            auth_file=os.path.join(os.path.dirname(__file__), '.auth', args.auth_file)
        )
        logger.info(f"Using credentials: {credentials}")

        supabase_url = os.getenv('SUPABASE_URL')
        supabase_key = os.getenv('SUPABASE_SERVICE_ROLE_KEY') or os.getenv('SUPABASE_ANON_KEY')
        supabase_client = create_supabase_client(supabase_url, supabase_key)
        scraper = LinkedInJobScraper(credentials, worker_id=0, supabase_client=supabase_client)
        base_url = args.linkedin_url.rstrip('/')
        linkedin_jobs_url = f"{base_url}/jobs/"
        
        try:
            jobs = scraper.scrape_company_jobs(linkedin_jobs_url, args.organization_name, dir_prefix_date, args.headless)
            if jobs:
                logger.info(f"[Worker 0] Successfully scraped {len(jobs)} jobs from {args.organization_name}")
                handle_job_db_operations(supabase_client, jobs, args.organization_name, dir_prefix_date, args.company_id)
            else:
                logger.error("[Worker 0] No jobs were scraped")

            # Set isScrapedToday to True after processing
            try:
                supabase_client.table("Company").update({"isScrapedToday": True}).eq("id", args.company_id).execute()
                logger.info(f"Set isScrapedToday=True for company {args.company_id}")
            except Exception as e:
                logger.error(f"Failed to update isScrapedToday for company {args.company_id}: {str(e)}")
        except Exception as e:
            logger.error(f"Error processing company {args.company_id}: {str(e)}")
    else:
        logger.error("Either use --from-db for all companies, or provide --linkedin-url, --organization-name, and --company-id for a single company.")

if __name__ == "__main__":
    main() 