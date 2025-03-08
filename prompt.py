JOB_CARD_PROMPT = """**Analyze the following HTML and extract a regex pattern that accurately captures job URLs, ensuring no duplicates.** The extracted URLs should be:  

1. **Precise** – The regex should target only valid job URLs.  
2. **Unique** – Prevent duplicate URLs by ensuring a strict match that excludes repeated or tracking variations.  
3. **Context-Aware** – The pattern should be anchored to specific job card structures in the HTML.  
4. **Minimal False Positives** – Ensure that only valid job listing URLs are captured, avoiding irrelevant links.

---

### **Input Format:**  
```html
{markdown}
```

---

### **Output Requirements:**  
1. **Regex pattern for extracting job URLs.**  
2. **Explanation of the regex pattern,** detailing:  
   - **Why it works** for the given HTML structure.  
   - **How it ensures uniqueness**, avoiding duplicate URLs.  
   - **How it filters out tracking parameters or extra attributes.**  
3. **Improvements if needed,** such as:  
   - Restricting matches to specific `<a>` tags inside job cards.  
   - Using `data-*` attributes if available for better reliability.  
   - Avoiding duplicate extraction of the same base URL.

### **Additional Considerations:**  
✅ **Ensure extracted URLs are absolute links** (convert relative URLs if necessary).  
✅ **Use regex anchors (`^`, `$`) or word boundaries** to make matches stricter.  
✅ **Filter tracking parameters** (e.g., `?utm_source`, `?ref=`) if present.  

"""

JOB_EXTRACTION_PROMPT = '''**Extract the following details from the given job posting page.** Ensure the extracted information is structured, formatted clearly, and follows the descriptions provided below. **Do not add, remove, or summarize any words—extract the text exactly as it appears.** This is crucial for maintaining the original structure, as the extracted data will be used for regex pattern analysis.

1. **Company Name**: The name of the company offering the job position.  
2. **Company Industry**: The industry or sector to which the company belongs.
3. **Job Title**: The official title of the job role being offered.  
4. **Job Type**: The type of employment contract (e.g., Full-time, Part-time, Contract). Default to Full-time if not specified.  
5. **Location**: The work location, specifying whether it is **Remote**, **On-site**, or **Hybrid**. 
6. **Full Job Description**: A detailed description of the job role, including its purpose, scope, and expectations.  
7. **List of Responsibilities**: A structured list outlining the key duties and responsibilities associated with this role.  
8. **List of Requirements**: A structured list of the qualifications, skills, and experience required for this role.
9. **List of Benefits**: A structured list of the benefits and perks offered to employees in this role.  

⚠️ **Strict Extraction Rule:** The extracted content must match the original job posting exactly—no modifications, omissions, or rewording. The goal is to preserve the original text verbatim for accurate pattern analysis.
'''

REGEX_EXTRACTION_PROMPT = '''Given the extracted job posting data and its corresponding **HTML page**, derive a set of **regex patterns** that can be used to accurately extract the same information directly from similar job posting pages.

**Input:**  
1. **Extracted Job Data:** The structured data that was extracted from the job posting page. This includes fields like Company Name, Job Title, Job Type, Location, etc.  
2. **HTML Page Source:** The raw HTML content of the job posting page.

---

### **Task Requirements:**
1. **Identify Unique Patterns:** Analyze the **HTML structure** and **extracted job data** to determine distinct patterns for each field.
2. **Construct Regex Patterns:** Generate regex rules that can be used to extract each field **accurately and reliably** from similar job postings.
3. **Ensure Robustness:** The regex should be:
   - **Precise**: It should match only the intended data field.
   - **Generalizable**: It should work across similar job postings while minimizing false positives.
   - **Anchored to HTML Structure**: Utilize surrounding tags, attributes, or contextual markers (e.g., class names, data attributes).
4. **Output Format:**  
   - Provide regex patterns for each field.  
   - If multiple patterns are needed (e.g., due to variations in structure), list them all.  
   - If a field cannot be extracted reliably using regex, return empty string for regex pattern but provide explaination. Provide an explanation and suggest alternative parsing strategies such as:
      - XPath (if the structure is consistent but difficult to capture with regex).
      - CSS Selectors (if elements are structured with unique class names).
      - NLP-based text parsing (if the field is embedded in an unstructured block of text).

⚠️ **Important Notes:**
- The regex patterns should be as **specific as possible** to avoid extracting unrelated text.
- If the structure varies significantly, provide **alternative regex patterns** or suggest **fallback parsing techniques** (e.g., XPath, BeautifulSoup).
- If a field spans multiple lines or contains HTML elements, adjust the regex accordingly (e.g., using `[\s\S]*?` for multi-line text).

Extracted Job Data:
{extracted_data}

HTML Page Source:
{html}
'''