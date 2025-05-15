import os
from playwright.sync_api import sync_playwright
from dotenv import load_dotenv

# Load environment variables
load_dotenv()

def authenticate_jobright():
    """
    Authenticate with JobRight.ai and save the authentication state.
    Returns the storage state path if successful, None otherwise.
    """
    auth_file = os.path.join(os.path.dirname(__file__), '.auth', 'jobright_auth.json')
    
    # Create .auth directory if it doesn't exist
    os.makedirs(os.path.dirname(auth_file), exist_ok=True)
    
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False)  # Set to True in production
        context = browser.new_context()
        page = context.new_page()
        
        try:
            # Navigate to JobRight signup page
            page.goto('https://jobright.ai/?login=true')
            
            # Wait for the page to load
            page.wait_for_load_state('networkidle')
            
            # Fill in signup/login credentials
            # Note: Update these selectors based on actual JobRight form elements
            page.fill('#basic_email', os.getenv('JOBRIGHT_EMAIL'))
            page.fill('#basic_password', os.getenv('JOBRIGHT_PASSWORD'))
            
            # Click the sign in/submit button
            # Note: Update this selector based on actual JobRight button
            page.click('form#basic button[type="submit"]')
            
            # Wait for navigation to complete and verify we're logged in
            # Note: Update this URL based on actual JobRight dashboard URL
            page.wait_for_url('https://jobright.ai/onboarding-v3/mode-selection?login=true', timeout=30000)
            
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
    Create a new browser context with saved JobRight authentication state.
    """
    auth_file = os.path.join(os.path.dirname(__file__), '.auth', 'jobright_auth.json')
    
    if not os.path.exists(auth_file):
        print("No saved authentication state found. Please run authenticate_jobright() first.")
        return None
    
    browser = playwright.chromium.launch(headless=False)  # Set to True in production
    context = browser.new_context(storage_state=auth_file)
    return context

if __name__ == "__main__":
    # Run authentication
    auth_file = authenticate_jobright()
    
    if auth_file:
        print("Authentication successful!")
        
        # Test the saved authentication
        with sync_playwright() as p:
            context = create_authenticated_context(p)
            if context:
                page = context.new_page()
                page.goto('https://jobright.ai/onboarding-v3/mode-selection')  # Update with actual dashboard URL
                # Wait a bit to verify we're logged in
                page.wait_for_timeout(5000)
                context.close() 