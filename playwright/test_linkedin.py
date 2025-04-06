import pytest
from playwright.sync_api import sync_playwright
from linkedin_auth import create_authenticated_context

def test_linkedin_feed():
    """
    Test accessing LinkedIn feed using saved authentication.
    """
    with sync_playwright() as p:
        # Create an authenticated context
        context = create_authenticated_context(p)
        assert context is not None, "Failed to create authenticated context"
        
        page = context.new_page()
        
        # Navigate to LinkedIn feed
        page.goto('https://www.linkedin.com/feed/')
        
        # Verify we're on the feed page (you can add more specific assertions)
        assert 'feed' in page.url
        
        # Example: Check if we can see the post creation box
        post_button = page.get_by_role("button", name="Start a post")
        assert post_button.is_visible()
        
        context.close()

def test_linkedin_profile():
    """
    Test accessing LinkedIn profile using saved authentication.
    """
    with sync_playwright() as p:
        context = create_authenticated_context(p)
        assert context is not None, "Failed to create authenticated context"
        
        page = context.new_page()
        
        # Navigate to LinkedIn profile
        page.goto('https://www.linkedin.com/in/me')
        
        # Verify we're on a profile page
        assert 'linkedin.com/in/' in page.url
        
        context.close() 