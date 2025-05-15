from playwright.sync_api import sync_playwright
import json
from jobright_auth import create_authenticated_context
from tqdm import tqdm

def scrape_job_categories_and_titles(context):
    page = context.new_page()
    page.goto('https://jobright.ai/onboarding-v3/diagnostics')
    page.wait_for_load_state('networkidle')

    # Click the 'Job Function' input box to show categories
    page.click('input[placeholder="Please select/enter your expected job function"]')
    page.wait_for_timeout(500)  # Wait for dropdown to appear

    # Get all category elements
    categories = page.query_selector_all('div.index_job-title-selector-category-item__xMsEI')
    result = {}

    with tqdm(total=len(categories), desc='Categories') as pbar:
        for i, category in enumerate(categories):
            category_name = category.inner_text().strip()
            pbar.set_postfix_str(f'Processing: {category_name}')
            category.click()
            page.wait_for_timeout(500)  # Wait for job titles to update

            # Find all sub-category titles
            sub_categories = page.query_selector_all('span.ant-typography.index_job-title-selector-sub-category-title___PJt6.css-1mxm6dm')

            category_dict = {}

            for sub_cat in sub_categories:
                sub_cat_name = sub_cat.inner_text().strip()
                # The next sibling div contains the job titles
                job_titles_div = sub_cat.evaluate_handle('node => node.nextElementSibling')
                job_titles = job_titles_div.query_selector_all('span.ant-tag.index_job-title-selector-title-tag__ttN6H.undefined.css-1mxm6dm')

                titles = [jt.inner_text().strip() for jt in job_titles]
                category_dict[sub_cat_name] = titles

            result[category_name] = category_dict
            pbar.update(1)

    # Save the result to a JSON file
    with open('jobright_job_categories.json', 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print('Results saved to jobright_job_categories.json')
    page.close()

if __name__ == '__main__':
    with sync_playwright() as p:
        context = create_authenticated_context(p)
        if context:
            scrape_job_categories_and_titles(context)
            context.close() 