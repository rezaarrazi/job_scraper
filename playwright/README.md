# LinkedIn Job Scraper

A Python script using Playwright to scrape job listings from LinkedIn company pages.

## Features

- Automated login to LinkedIn
- Saves authentication state to avoid repeated logins
- Scrapes job titles, descriptions, locations, and posting dates
- Saves results to JSON file with timestamp
- Handles rate limiting with delays
- Error handling for failed job extractions

## Prerequisites

1. Python 3.7+
2. Playwright
3. LinkedIn account credentials

## Installation

1. Install required packages:
```bash
pip install playwright python-dotenv
playwright install  # Install browser binaries
```

2. Create a `.env` file in the project root with your LinkedIn credentials:
```
LINKEDIN_USERNAME=your_email@example.com
LINKEDIN_PASSWORD=your_password
```

## Usage

1. Basic usage with default settings:
```python
from linkedin_jobs_scraper import LinkedInJobScraper

scraper = LinkedInJobScraper()
jobs = scraper.scrape_company_jobs("https://www.linkedin.com/company/company-name/jobs/")
```

2. Run the script directly:
```bash
python linkedin_jobs_scraper.py
```

The script will:
- Login to LinkedIn (if needed)
- Navigate to the company's jobs page
- Scrape all available job listings
- Save the results to a JSON file with timestamp

## Output Format

The script saves job listings in JSON format:
```json
[
  {
    "title": "Software Engineer",
    "company": "Company Name",
    "location": "City, Country",
    "description": "Full job description...",
    "posted_date": "Posted 2 days ago"
  },
  ...
]
```

## Notes

- The script uses a non-headless browser by default for development (you can see the automation happening). Set `headless=True` in production.
- Authentication state is saved in `.auth/linkedin_auth.json` for reuse
- Includes delay between job extractions to avoid rate limiting
- Error handling ensures the script continues even if individual job extractions fail

## Limitations

- LinkedIn may detect and block automated access
- Job details availability depends on your LinkedIn account access level
- Some job posts might require additional clicks/interactions to view full details

## Legal Considerations

Ensure you comply with LinkedIn's terms of service and robots.txt when using this scraper. This tool is for educational purposes only. 