"""
This module provides a FastAPI application that uses Playwright to fetch and return
the HTML content of a specified URL. It supports optional proxy settings and media blocking.
"""

from os import environ
import re
from urllib.parse import urlparse

from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from playwright.async_api import Browser, async_playwright
from pydantic import BaseModel
from get_error import get_error

BROWSERBASE_API_KEY = environ.get("BROWSERBASE_API_KEY")
BLOCK_MEDIA = environ.get("BLOCK_MEDIA", "False").upper() == "TRUE"

app = FastAPI()

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

    url_domain = urlparse(body.url).netloc
    
    if url_domain in ["twitter.com", "x.com"]:
        body.url = await transform_twitter_url(body.url)
    elif url_domain == "reddit.com" or url_domain.endswith(".reddit.com"):
        return await handle_reddit_url(body)
    
    # First attempt with regular browser
    result = await fetch_with_regular_browser(body)
    
    # If status code is 403, try with Browserbase
    if result["pageStatusCode"] == 403:
        browserbase_result = await fetch_with_browserbase(body)
        return JSONResponse(content=browserbase_result)
    
    return JSONResponse(content=result)

async def transform_twitter_url(url: str) -> str:
    """Transform Twitter URL to the corresponding API endpoint."""
    tweet_id_match = re.search(r'/status/(\d+)', url)
    if not tweet_id_match:
        raise HTTPException(status_code=400, detail="Invalid Twitter URL")
    
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

async def fetch_with_browserbase(body: UrlModel):
    async with async_playwright() as playwright:
        try:
            chromium = playwright.chromium
            browser = await chromium.connect_over_cdp(f"wss://connect.browserbase.com?apiKey={BROWSERBASE_API_KEY}")
            
            context = browser.contexts[0]
            page = context.pages[0]
            
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
            