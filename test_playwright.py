import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/114.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        # Navigate to Sofascore JSON endpoint
        response = await page.goto("https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-06-20")
        print(f"Status: {response.status}")
        text = await response.text()
        print(f"Length of response: {len(text)}")
        await browser.close()

asyncio.run(main())
