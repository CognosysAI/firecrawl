"""
This module provides a FastAPI application that uses Playwright to fetch and return
the HTML content of a specified URL. It supports optional proxy settings and media blocking.
"""

from os import environ
import random
import re
from urllib.parse import urlencode, urlparse
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel
import requests
from get_error import get_error

BROWSERBASE_API_KEY = environ.get("BROWSERBASE_API_KEY")
BROWSERBASE_PROJECT_ID = environ.get("BROWSERBASE_PROJECT_ID")
OLOSTEP_API_KEY = environ.get("OLOSTEP_API_KEY")
BLOCK_MEDIA = environ.get("BLOCK_MEDIA", "False").upper() == "TRUE"
PROXY_DOMAINS = ["crunchbase.com"]

app = FastAPI()

# Configure logging
logging.basicConfig(level=logging.ERROR)
logger = logging.getLogger(__name__)

class UrlModel(BaseModel):
    """Model representing the URL and associated parameters for the request."""
    url: str
    wait_after_load: int = 0
    timeout: int = 15000
    headers: dict = None

browser: Browser = None

@app.on_event("startup")
async def startup_event():
    """Event handler for application startup to initialize the browser."""
    global browser
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()

@app.on_event("shutdown")
async def shutdown_event():
    """Event handler for application shutdown to close the browser."""
    await browser.close()

@app.get("/health/liveness")
def liveness_probe():
    """Endpoint for liveness probe."""
    return JSONResponse(content={"status": "ok"}, status_code=200)


@app.get("/health/readiness")
async def readiness_probe():
    """Endpoint for readiness probe. Checks if the browser instance is ready."""
    if browser:
        return JSONResponse(content={"status": "ok"}, status_code=200)
    return JSONResponse(content={"status": "Service Unavailable"}, status_code=503)


@app.post("/html")
async def root(body: UrlModel):
    """
    Endpoint to fetch and return HTML content of a given URL.

    Args:
        body (UrlModel): The URL model containing the target URL, wait time, and timeout.

    Returns:
        JSONResponse: The HTML content of the page.
    """
    try:
        url_domain = urlparse(body.url).netloc
        
        if url_domain in ["twitter.com", "x.com"]:
            body.url = await transform_twitter_url(body.url)
        elif url_domain == "reddit.com" or url_domain.endswith(".reddit.com"):
            return await handle_reddit_url(body)
        elif url_domain == "linkedin.com" or url_domain.endswith(".linkedin.com"):
            content = scrape_url_with_olostep(body.url)
            return JSONResponse(content={"content": content, "pageStatusCode": 200, "pageError": ""})
        elif url_domain == "crunchbase.com" or url_domain.endswith(".crunchbase.com"):
            content = scrape_url_with_olostep(body.url)
            return JSONResponse(content={"content": content, "pageStatusCode": 200, "pageError": ""})
        elif url_domain == "dnb.com" or url_domain.endswith(".dnb.com"):
            browserbase_result = await fetch_with_browserbase(body)
            return JSONResponse(content=browserbase_result)

        
        # First attempt with regular browser
        try:
            result = await fetch_with_regular_browser(body)
            
            # If status code is 403, we'll use Browserbase instead
            if result["pageStatusCode"] == 403:
                logger.info(f"Received 403 status for URL {body.url}. Attempting to fetch with Browserbase.")
                raise Exception("Received 403 status code")
            
            return JSONResponse(content=result)
        except Exception as e:
            logger.error(f"Error with regular browser for URL {body.url}: {str(e)}")
            logger.error("Attempting to fetch with Browserbase as fallback")
            return JSONResponse(content=await fetch_with_browserbase(body))
        
    except Exception as e:
        logger.error(f"An error occurred while processing the request for URL: {body.url}")
        logger.error(f"Request details: wait_after_load={body.wait_after_load}, timeout={body.timeout}, headers={body.headers}")
        logger.error(f"Error details: {str(e)}", exc_info=True)
        return JSONResponse(content={"content": "", "pageStatusCode": 500, "pageError": "Internal Server Error"})

async def transform_twitter_url(url: str) -> str:
    """Transform Twitter URL to the corresponding API endpoint."""
    tweet_id_match = re.search(r'/status/(\d+)', url)
    if not tweet_id_match:
        return url
        # raise HTTPException(status_code=400, detail="Invalid Twitter URL")
    
    tweet_id = tweet_id_match.group(1)
    return f"https://cdn.syndication.twimg.com/tweet-result?id={tweet_id}&lang=en&features=tfw_timeline_list%3A%3Btfw_follower_count_sunset%3Atrue%3Btfw_tweet_edit_backend%3Aon%3Btfw_refsrc_session%3Aon%3Btfw_fosnr_soft_interventions_enabled%3Aon%3Btfw_show_birdwatch_pivots_enabled%3Aon%3Btfw_show_business_verified_badge%3Aon%3Btfw_duplicate_scribes_to_settings%3Aon%3Btfw_use_profile_image_shape_enabled%3Aon%3Btfw_show_blue_verified_badge%3Aon%3Btfw_legacy_timeline_sunset%3Atrue%3Btfw_show_gov_verified_badge%3Aon%3Btfw_show_business_affiliate_badge%3Aon%3Btfw_tweet_edit_frontend%3Aon&token=4c2mmul6mnh"


async def fetch_with_regular_browser(body: UrlModel):
    context = await browser.new_context()

    if BLOCK_MEDIA:
        await context.route(
            "**/*.{png,jpg,jpeg,gif,svg,mp3,mp4,avi,flac,ogg,wav,webm}",
            handler=lambda route, request: route.abort(),
        )

    page = await context.new_page()

    # Set headers if provided
    if body.headers:
        await page.set_extra_http_headers(body.headers)

    response = await page.goto(
        body.url,
        wait_until="load",
        timeout=body.timeout,
    )
    page_status_code = response.status
    page_error = get_error(page_status_code)
    
    if body.wait_after_load > 0:
        await page.wait_for_timeout(body.wait_after_load)

    page_content = await page.content()
    await context.close()
    
    return {
        "content": page_content,
        "pageStatusCode": page_status_code,
        "pageError": page_error
    }

def generate_random_fingerprint():
    fingerprint = {}

    # Randomize operating systems
    os_options = ["android", "ios", "linux", "macos", "windows"]
    selected_os = random.choice(os_options)
    fingerprint["operatingSystems"] = [selected_os]

    # Set devices based on the selected operating system
    if selected_os in ["android", "ios"]:
        fingerprint["devices"] = ["mobile"]
    else:  # linux, macos, windows
        fingerprint["devices"] = ["desktop"]

    # Randomize locales (this is a simplified list, you might want to expand it)
    locales = ["en-US", "en-GB", "es-ES", "fr-FR", "de-DE", "it-IT", "ja-JP", "zh-CN"]
    fingerprint["locales"] = random.sample(locales, 1)

    # Randomize browsers based on operating system
    if selected_os == "ios":
        browser_options = ["safari"]
    elif selected_os == "android":
        browser_options = ["chrome"]
    elif selected_os == "macos":
        browser_options = ["chrome", "firefox", "safari"]
    elif selected_os == "linux":
        browser_options = ["chrome", "firefox"]
    else:  # windows
        browser_options = ["chrome", "firefox", "edge"]
    fingerprint["browsers"] = random.sample(browser_options, 1)

    # # Randomize screen dimensions
    # if "mobile" in fingerprint["devices"]:
    #     fingerprint["screen"] = {
    #         "minWidth": random.randint(320, 414),
    #         "minHeight": random.randint(568, 896)
    #     }
    # else:
    #     fingerprint["screen"] = {
    #         "minWidth": random.randint(1024, 1920),
    #         "minHeight": random.randint(768, 1080)
    #     }
    # fingerprint["screen"]["maxWidth"] = fingerprint["screen"]["minWidth"]
    # fingerprint["screen"]["maxHeight"] = fingerprint["screen"]["minHeight"]

    return fingerprint

def create_session(useProxy: bool = False):
    url = "https://www.browserbase.com/v1/sessions"
    headers = {
        "Content-Type": "application/json",
        "x-bb-api-key": BROWSERBASE_API_KEY,
    }
    json = {
        "projectId": BROWSERBASE_PROJECT_ID,
        "browserSettings": {
          # Fingerprint options
          "fingerprint": generate_random_fingerprint()
        },
        "proxies": useProxy
    }
    response = requests.post(url, json=json, headers=headers)
    return response.json()["id"]

async def fetch_with_browserbase(body: UrlModel):
    async with async_playwright() as playwright:
        try:
            # useProxy = any(domain in body.url for domain in PROXY_DOMAINS)
            # session_id = create_session(useProxy)
            chromium = playwright.chromium
            browser = await chromium.connect_over_cdp(f"wss://connect.browserbase.com?apiKey={BROWSERBASE_API_KEY}")
            # browser = await chromium.connect_over_cdp(f"wss://connect.browserbase.com?apiKey={BROWSERBASE_API_KEY}&sessionId={session_id}")
            
            context = browser.contexts[0]
            page = context.pages[0]
            
            if body.headers:
                await page.set_extra_http_headers(body.headers)

            response = await page.goto(
                body.url,
                wait_until="load",
                timeout=body.timeout,
            )

            # Check for challenge validation
            title = await page.title()
            if "dnb.com" in body.url and title == "Challenge Validation":
                # Wait for the challenge to complete
                try:
                    await page.wait_for_function(
                        """() => {
                            return document.title !== "Challenge Validation";
                        }""",
                        timeout=30000
                            )
                except TimeoutError:
                    print("Timeout waiting for challenge validation to complete")

            page_status_code = response.status
            page_error = get_error(page_status_code)

            if body.wait_after_load > 0:
                await page.wait_for_timeout(body.wait_after_load)

            page_content = await page.content()

            await context.close()
            await page.close()
            await browser.close()
            
            return {
                "content": page_content,
                "pageStatusCode": page_status_code,
                "pageError": page_error
            }
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"An error occurred with Browserbase: {str(e)}")

async def handle_reddit_url(body: UrlModel):
    async with async_playwright() as playwright:
        try:
            chromium = playwright.chromium
            browser = await chromium.connect_over_cdp(f"wss://connect.browserbase.com?apiKey={BROWSERBASE_API_KEY}&enableProxy=true")
            
            context = browser.contexts[0]
            page = context.pages[0]
            # Set headers if provided
            if body.headers:
                await page.set_extra_http_headers(body.headers)

            response = await page.goto(
                body.url,
                wait_until="domcontentloaded",
            )

            await page.wait_for_selector('shreddit-comment[depth="0"]', timeout=10000)

            await page.evaluate("window.scrollTo(0, document.body.scrollHeight)")

            await page.wait_for_timeout(1000)

            page_status_code = response.status
            page_error = get_error(page_status_code)

            reddit_data = await extract_reddit_data(page, body.url)

            await context.close()
            await page.close()
            await browser.close()
            json_compatible_item_data = {
                "content": reddit_data,
                "pageStatusCode": page_status_code,
                "pageError": page_error
            }
            return JSONResponse(content=json_compatible_item_data)
        except Exception as e:
            raise HTTPException(status_code=500, detail=f"An error occurred: {str(e)}")

async def extract_reddit_data(page, url):
    # Extract comments
    comments = await page.evaluate("""
        () => {
            const commentElements = document.querySelectorAll('shreddit-comment[depth="0"]');
            return Array.from(commentElements).map((comment) => {
                const author = comment.getAttribute("author");
                const contentElement = comment.querySelector(".md");
                const content = contentElement ? contentElement.textContent.trim() : "";
                const score = comment.getAttribute("score");
                return { author, content, score };
            });
        }
    """)

    # Extract title and body
    post_data = await page.evaluate("""
        () => {
            const titleElement = document.querySelector('h1[slot="title"]');
            const bodyElement = document.querySelector('div[slot="text-body"] .md');
            return {
                title: titleElement?.textContent?.trim() || "Title not found",
                body: bodyElement?.textContent?.trim() || "No text body found",
            };
        }
    """)

    # Format the response as markdown with XML tags
    markdown_response = f"<title>{post_data['title']}</title>\n\n<body>{post_data['body']}</body>\n\n## Top Comments\n\n"
    
    for comment in comments:
        # If you want to include author and score, uncomment the following line:
        # markdown_response += f"<comment>\n<author>{comment['author']}</author>\n<score>{comment['score']}</score>\n<content>{comment['content']}</content>\n</comment>\n\n"
        markdown_response += f"<comment>{comment['content']}</comment>\n\n"

    return markdown_response
            
def scrape_url_with_olostep(url: str):
    params = {
        "url": url,
        "timeout": "20",
        "waitBeforeScraping": "1",
        "saveHtml": "false",
        "saveMarkdown": "false",
        "removeCSSselectors": "default",
        "htmlTransformer": "true",
        "removeImages": "true",
        "expandMarkdown": "false",
        "expandHtml": "true",
        "fastLane": "true",
    }
    
    url_to_scrape = f"https://agent.olostep.com/olostep-p2p-incomingAPI?{urlencode(params)}"
    
    try:
        response = requests.get(url_to_scrape, headers={
            "Authorization": f"Bearer {OLOSTEP_API_KEY}"
        })
        response.raise_for_status()
        data = response.json()
        return data.get("html_content", "")
    except requests.RequestException as e:
        raise HTTPException(status_code=500, detail=f"Failed to scrape website URL {url} with Olostep: {str(e)}")