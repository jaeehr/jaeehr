#!/usr/bin/env python3
"""
Checks Alhambra ticket availability for June 7-8, 2026.
Sends email notification when tickets are found.
"""

import asyncio
import os
import smtplib
import sys
from datetime import datetime, timezone
from email.mime.image import MIMEImage
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path

from playwright.async_api import TimeoutError as PlaywrightTimeout
from playwright.async_api import async_playwright

TICKET_URL = "https://tickets.alhambra-patronato.es/en/producto/alhambra-general/"
TARGET_YEAR = 2026
TARGET_MONTH = 6  # June
TARGET_DAYS = [7, 8]

SMTP_SERVER = "smtp.gmail.com"
SMTP_PORT = 587


async def navigate_to_month(page, target_year: int, target_month: int) -> bool:
    """Navigate the calendar to the target month/year. Returns True if successful."""
    # Selectors to find the current month label
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
    # Selectors for "next month" navigation
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

    for attempt in range(24):  # up to 24 months forward
        # Read current month from the calendar
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

        # Click next month
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

        # Strategy 1: jQuery UI datepicker — available days have a clickable <a>
        # Unavailable days have class ui-datepicker-unselectable or ui-state-disabled
        selectors_available = [
            # Standard jQuery UI datepicker: td not disabled, containing <a> with day text
            f"td:not(.ui-datepicker-unselectable):not(.ui-state-disabled) a:text-is('{day_str}')",
            # Generic available cell
            f"td.available:has-text('{day_str}')",
            f"td[class*='available']:has-text('{day_str}')",
            # Bookable (WooCommerce bookings plugin)
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
            # Strategy 2: look for the day cell and check it's not disabled
            try:
                # Find all td cells containing exactly this day number
                cells = page.locator(f"td").filter(has_text=day_str)
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
                print(f"  Day {day}: error during strategy 2 — {e}")

        if found_available:
            available.append(day)
        else:
            print(f"  Day {day}: not available")

    return available


async def main() -> int:
    notify_email = os.environ.get("NOTIFY_EMAIL", "jaer978@gmail.com")
    smtp_user = os.environ.get("GMAIL_USER", "")
    smtp_pass = os.environ.get("GMAIL_APP_PASSWORD", "")

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
        # Hide webdriver flag
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

            # Check for bot challenge / access denied
            page_title = await page.title()
            page_text = (await page.inner_text("body"))[:500]
            print(f"  Title: {page_title!r}")
            if any(kw in page_text.lower() for kw in ["403", "forbidden", "access denied", "captcha"]):
                print("  WARNING: Possible bot challenge detected. Check the screenshot.")
                await page.screenshot(path="step1_loaded.png")
                await browser.close()
                return 2

            # Navigate calendar to June 2026
            print("Navigating calendar to June 2026...")
            reached = await navigate_to_month(page, TARGET_YEAR, TARGET_MONTH)
            await page.screenshot(path="step2_calendar.png", full_page=False)
            print("  Screenshot: step2_calendar.png")

            if not reached:
                print("  WARNING: Could not confirm calendar reached June 2026. Attempting date check anyway.")

            # Check availability
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
        if smtp_user and smtp_pass:
            send_email(
                smtp_user=smtp_user,
                smtp_pass=smtp_pass,
                to_email=notify_email,
                available_days=available_days,
            )
        else:
            print("GMAIL_USER / GMAIL_APP_PASSWORD not set — skipping email")
        return 0
    else:
        print("\nNo tickets available for June 7-8 right now.")
        return 0


def send_email(smtp_user: str, smtp_pass: str, to_email: str, available_days: list[int]) -> None:
    days_str = " and ".join(f"June {d}, 2026" for d in available_days)
    subject = f"Alhambra tickets AVAILABLE: {days_str}"
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")

    msg = MIMEMultipart("related")
    msg["Subject"] = subject
    msg["From"] = smtp_user
    msg["To"] = to_email

    html_body = f"""\
<html><body>
<h2>Alhambra tickets are available!</h2>
<p>Tickets for <strong>{days_str}</strong> appear to be available on the official site.</p>
<p><a href="{TICKET_URL}" style="font-size:18px;color:#c00;">Book now &rarr;</a></p>
<p style="color:#666;">Checked at {now}</p>
<hr>
<p>Screenshot from the booking page:</p>
<img src="cid:screenshot" style="max-width:100%;border:1px solid #ccc;">
</body></html>
"""
    msg.attach(MIMEText(html_body, "html"))

    screenshot = Path("step3_result.png")
    if not screenshot.exists():
        screenshot = Path("step2_calendar.png")
    if screenshot.exists():
        with open(screenshot, "rb") as f:
            img = MIMEImage(f.read())
            img.add_header("Content-ID", "<screenshot>")
            img.add_header("Content-Disposition", "inline", filename=screenshot.name)
            msg.attach(img)

    print(f"Sending email to {to_email}...")
    with smtplib.SMTP(SMTP_SERVER, SMTP_PORT) as server:
        server.ehlo()
        server.starttls()
        server.login(smtp_user, smtp_pass)
        server.send_message(msg)
    print("Email sent!")


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
