import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv
from utils.job_scraper import authenticate_linkedin, create_authenticated_context
import argparse

# Load environment variables
load_dotenv()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="LinkedIn Authentication Script")
    parser.add_argument('--auth-file-path', type=str, default=None, help='Path to save/load LinkedIn authentication file')
    args = parser.parse_args()

    # Run authentication
    auth_file = authenticate_linkedin(
        os.getenv('LINKEDIN_USERNAME'),
        os.getenv('LINKEDIN_PASSWORD'),
        save_auth_file=True,
        auth_file=args.auth_file_path
    )
    
    if auth_file:
        print("Authentication successful!")
        
        # Test the saved authentication
        with sync_playwright() as p:
            context = create_authenticated_context(p, auth_file=args.auth_file_path)
            if context:
                page = context.new_page()
                page.goto('https://www.linkedin.com/feed/')
                # Wait a bit to verify we're logged in
                page.wait_for_timeout(5000)
                context.close() 