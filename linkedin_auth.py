import os
from playwright_scripts.sync_api import sync_playwright
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def authenticate_linkedin():
    """
    Authenticate with LinkedIn and save the authentication state.
    Returns the storage state path if successful, None otherwise.
    """
    auth_file = os.path.join(os.path.dirname(__file__), '.auth', 'linkedin_auth.json')
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Set to True in production
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to LinkedIn login page
            page.goto('https://www.linkedin.com/login')
            
            # Fill in login credentials
            page.fill('#username', os.getenv('LINKEDIN_USERNAME'))
            page.fill('#password', os.getenv('LINKEDIN_PASSWORD'))
            
            # Click the sign in button
            page.click('button[type="submit"]')
            
            # Wait for navigation to complete and verify we're logged in
            page.wait_for_url('https://www.linkedin.com/feed/')
            
            # Save storage state
            context.storage_state(path=auth_file)
            print(f"Authentication state saved to {auth_file}")
            
            return auth_file
            
        except Exception as e:
            print(f"Authentication failed: {str(e)}")
            return None
        finally:
            browser.close()

def create_authenticated_context(playwright):
    """
    Create a new browser context with saved authentication state.
    """
    auth_file = os.path.join(os.path.dirname(__file__), '.auth', 'linkedin_auth.json')
    
    if not os.path.exists(auth_file):
        print("No saved authentication state found. Please run authenticate_linkedin() first.")
        return None
    
    browser = playwright.chromium.launch(headless=False)  # Set to True in production
    context = browser.new_context(storage_state=auth_file)
    return context

if __name__ == "__main__":
    # Run authentication
    auth_file = authenticate_linkedin()
    
    if auth_file:
        print("Authentication successful!")
        
        # Test the saved authentication
        with sync_playwright() as p:
            context = create_authenticated_context(p)
            if context:
                page = context.new_page()
                page.goto('https://www.linkedin.com/feed/')
                # Wait a bit to verify we're logged in
                page.wait_for_timeout(5000)
                context.close() 