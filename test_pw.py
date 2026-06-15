import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True)
        context = await browser.new_context(
            user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        )
        page = await context.new_page()
        # Go to the main site first to get cookies/clear challenge
        await page.goto("https://www.sofascore.com/")
        await page.wait_for_timeout(2000)
        # Now fetch the API
        resp = await page.evaluate('''async () => {
            const res = await fetch("https://api.sofascore.com/api/v1/sport/football/scheduled-events/2026-06-12");
            return {status: res.status, text: await res.text()};
        }''')
        print(resp['status'])
        print(resp['text'][:200])
        await browser.close()

asyncio.run(main())
