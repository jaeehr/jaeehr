#!/usr/bin/env python3
"""
Checks Alhambra ticket availability for June 7-8, 2026.
Sends a push notification via ntfy.sh when tickets are found.
"""

import asyncio
import os
import sys
import urllib.request
from datetime import datetime, timezone

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

TICKET_URL = "https://tickets.alhambra-patronato.es/en/producto/alhambra-general/"
TARGET_YEAR = 2026
TARGET_MONTH = 6  # June
TARGET_DAYS = [7, 8]


async def navigate_to_month(page, target_year: int, target_month: int) -> bool:
    """Navigate the calendar to the target month/year. Returns True if successful."""
    month_label_selectors = [
        ".ui-datepicker-title",
        ".ui-datepicker-month",
        "[class*='month-title']",
        "[class*='calendar-month']",
        "h3.month",
        ".month-name",
        ".calendar-header",
        "[class*='month'][class*='year']",
    ]
    next_btn_selectors = [
        ".ui-datepicker-next",
        "a.next",
        "button.next",
        "[data-action='next']",
        "[aria-label='Next month']",
        "[aria-label='Next']",
        "[title='Next']",
        ".next-month",
        "[class*='next-month']",
        "[class*='nextMonth']",
    ]

    month_names = [
        "January", "February", "March", "April", "May", "June",
        "July", "August", "September", "October", "November", "December",
    ]
    target_month_name = month_names[target_month - 1]

    for attempt in range(24):
        current_label = None
        for sel in month_label_selectors:
            try:
                el = page.locator(sel).first
                if await el.count() > 0:
                    current_label = await el.inner_text()
                    break
            except Exception:
                pass

        if current_label:
            print(f"  Calendar shows: {current_label!r}")
            if target_month_name in current_label and str(target_year) in current_label:
                print(f"  Reached {target_month_name} {target_year}")
                return True

        if attempt == 0 and not current_label:
            print("  Warning: could not read calendar month label")
            return False

        clicked = False
        for sel in next_btn_selectors:
            try:
                btn = page.locator(sel).first
                if await btn.count() > 0:
                    await btn.click()
                    await page.wait_for_timeout(700)
                    clicked = True
                    break
            except Exception:
                pass

        if not clicked:
            print("  Could not find next-month button")
            return False

    return False


async def find_available_days(page, days: list[int]) -> list[int]:
    """Return the subset of `days` that appear available in the calendar."""
    available = []

    for day in days:
        day_str = str(day)
        found_available = False

        selectors_available = [
            f"td:not(.ui-datepicker-unselectable):not(.ui-state-disabled) a:text-is('{day_str}')",
            f"td.available:has-text('{day_str}')",
            f"td[class*='available']:has-text('{day_str}')",
            f"td.bookable:has-text('{day_str}')",
            f"td[data-date]:not(.sold-out):not(.disabled):not(.unavailable) >> text='{day_str}'",
        ]

        for sel in selectors_available:
            try:
                els = page.locator(sel)
                count = await els.count()
                if count > 0:
                    found_available = True
                    print(f"  Day {day}: AVAILABLE (matched: {sel!r})")
                    break
            except Exception:
                pass

        if not found_available:
            try:
                cells = page.locator("td").filter(has_text=day_str)
                count = await cells.count()
                for i in range(count):
                    cell = cells.nth(i)
                    cell_text = (await cell.inner_text()).strip()
                    if cell_text != day_str:
                        continue
                    class_attr = await cell.get_attribute("class") or ""
                    aria_disabled = await cell.get_attribute("aria-disabled") or ""
                    disabled_keywords = {
                        "disabled", "unavailable", "sold-out", "soldout",
                        "unselectable", "blocked", "closed",
                    }
                    is_disabled = (
                        aria_disabled == "true"
                        or any(kw in class_attr.lower() for kw in disabled_keywords)
                    )
                    if not is_disabled:
                        found_available = True
                        print(f"  Day {day}: AVAILABLE (class={class_attr!r})")
                        break
                    else:
                        print(f"  Day {day}: unavailable (class={class_attr!r})")
            except Exception as e:
                print(f"  Day {day}: error during check — {e}")

        if found_available:
            available.append(day)
        else:
            print(f"  Day {day}: not available")

    return available


def send_ntfy(topic: str, available_days: list[int]) -> None:
    days_str = " and ".join(f"June {d}" for d in available_days)
    title = "Alhambra tickets available!"
    message = f"Tickets for {days_str}, 2026 are available. Book now!"

    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=message.encode("utf-8"),
        headers={
            "Title": title,
            "Priority": "urgent",
            "Tags": "ticket,rotating_light",
            "Click": TICKET_URL,
        },
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=10) as resp:
        print(f"ntfy notification sent (status {resp.status})")


async def main() -> int:
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")

    print(f"[{datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}] Checking Alhambra tickets...")

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                "--no-sandbox",
                "--disable-setuid-sandbox",
                "--disable-blink-features=AutomationControlled",
            ],
        )
        context = await browser.new_context(
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
            viewport={"width": 1280, "height": 900},
            locale="en-US",
            extra_http_headers={
                "Accept-Language": "en-US,en;q=0.9",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
            },
        )
        await context.add_init_script(
            "Object.defineProperty(navigator, 'webdriver', {get: () => undefined})"
        )

        page = await context.new_page()

        try:
            print(f"Loading {TICKET_URL} ...")
            await page.goto(TICKET_URL, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            await page.screenshot(path="step1_loaded.png", full_page=False)
            print("  Page loaded. Screenshot: step1_loaded.png")

            page_title = await page.title()
            page_text = (await page.inner_text("body"))[:500]
            print(f"  Title: {page_title!r}")
            if any(kw in page_text.lower() for kw in ["403", "forbidden", "access denied", "captcha"]):
                print("  WARNING: Possible bot challenge detected. Check the screenshot.")
                await browser.close()
                return 2

            # Dismiss cookie consent popup if present
            cookie_btn_selectors = [
                "button:has-text('ACCEPT EVERYTHING')",
                "button:has-text('Accept everything')",
                "button:has-text('Accept all')",
                "button:has-text('REJECT EVERYTHING')",
                "button:has-text('Reject everything')",
                "#onetrust-accept-btn-handler",
                "[class*='cookie'] button",
                "[id*='cookie'] button",
            ]
            for sel in cookie_btn_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click()
                        print(f"  Dismissed cookie popup ({sel})")
                        await page.wait_for_timeout(1500)
                        break
                except Exception:
                    pass

            # Click "PURCHASE" button to reveal the date picker
            purchase_selectors = [
                "button:has-text('PURCHASE')",
                "a:has-text('PURCHASE')",
                "button:has-text('Purchase')",
                "[class*='purchase']",
                "[class*='buy']",
                "button:has-text('BUY')",
            ]
            for sel in purchase_selectors:
                try:
                    btn = page.locator(sel).first
                    if await btn.count() > 0:
                        await btn.click()
                        print(f"  Clicked purchase button ({sel})")
                        await page.wait_for_timeout(2000)
                        break
                except Exception:
                    pass

            await page.screenshot(path="step1b_after_cookie.png", full_page=False)
            print("  Screenshot: step1b_after_cookie.png")

            print("Navigating calendar to June 2026...")
            reached = await navigate_to_month(page, TARGET_YEAR, TARGET_MONTH)
            await page.screenshot(path="step2_calendar.png", full_page=False)
            print("  Screenshot: step2_calendar.png")

            if not reached:
                print("  WARNING: Could not confirm calendar reached June 2026. Attempting check anyway.")

            print(f"Checking availability for days: {TARGET_DAYS}")
            available_days = await find_available_days(page, TARGET_DAYS)
            await page.screenshot(path="step3_result.png", full_page=False)
            print("  Screenshot: step3_result.png")

        except PlaywrightTimeout as e:
            print(f"TIMEOUT: {e}", file=sys.stderr)
            try:
                await page.screenshot(path="error.png")
            except Exception:
                pass
            await browser.close()
            return 1
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            try:
                await page.screenshot(path="error.png")
            except Exception:
                pass
            await browser.close()
            return 1

        await browser.close()

    if available_days:
        days_str = " and ".join(f"June {d}" for d in available_days)
        print(f"\nTICKETS AVAILABLE: {days_str}!")
        if ntfy_topic:
            send_ntfy(ntfy_topic, available_days)
        else:
            print("NTFY_TOPIC not set — skipping push notification")
        return 0
    else:
        print("\nNo tickets available for June 7-8 right now.")
        return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
