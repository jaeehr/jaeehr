#!/usr/bin/env python3
"""
Checks Alhambra ticket availability for June 7-8, 2026.
Sends a push notification via ntfy.sh when tickets are found.
"""

import asyncio
import json
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

# Keywords that mean a date is NOT bookable
UNAVAILABLE_KEYWORDS = {
    "not-available", "notavailable", "not_available",
    "sold-out", "soldout", "sold_out",
    "disabled", "unavailable", "blocked", "closed",
    "unselectable", "ui-state-disabled", "ui-datepicker-unselectable",
    "no-disponible", "agotado",
}


async def dismiss_cookie_popup(page) -> None:
    selectors = [
        "button:has-text('REJECT EVERYTHING')",
        "button:has-text('Reject everything')",
        "button:has-text('ACCEPT EVERYTHING')",
        "button:has-text('Accept everything')",
        "button:has-text('Accept all')",
        "#onetrust-accept-btn-handler",
    ]
    for sel in selectors:
        try:
            btn = page.locator(sel).first
            if await btn.count() > 0:
                await btn.click()
                print(f"  Dismissed cookie popup via: {sel}")
                await page.wait_for_timeout(1000)
                return
        except Exception:
            pass


async def dump_calendar_cells(page) -> list[dict]:
    """Use JS to extract all calendar date cells so we can see their real classes."""
    return await page.evaluate("""
        () => {
            // Try many common selectors for calendar day cells
            const selectors = [
                'td[data-date]',
                'td.calendar-day',
                'td[class*="day"]',
                '.booking-calendar td',
                '.datepicker td',
                '.calendar td',
                'table.ui-datepicker-calendar td',
                '[class*="calendar"] td',
                '[class*="datepicker"] td',
            ];
            let cells = [];
            for (const sel of selectors) {
                const found = document.querySelectorAll(sel);
                if (found.length > 0) {
                    cells = Array.from(found);
                    break;
                }
            }
            // Fall back: all td elements that contain just a number 1-31
            if (cells.length === 0) {
                cells = Array.from(document.querySelectorAll('td')).filter(td => {
                    const t = td.textContent.trim();
                    return /^\\d{1,2}$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 31;
                });
            }
            return cells.map(td => ({
                text: td.textContent.trim(),
                className: td.className,
                dataDate: td.getAttribute('data-date'),
                ariaDisabled: td.getAttribute('aria-disabled'),
                ariaLabel: td.getAttribute('aria-label'),
                title: td.getAttribute('title'),
                hasLink: td.querySelector('a') !== null,
            }));
        }
    """)


def is_available(cell: dict) -> bool:
    """Decide if a calendar cell represents an available date."""
    class_lower = (cell.get("className") or "").lower()
    aria_disabled = (cell.get("ariaDisabled") or "").lower()

    if aria_disabled == "true":
        return False
    if any(kw in class_lower for kw in UNAVAILABLE_KEYWORDS):
        return False
    # If it has a link it's almost certainly bookable
    if cell.get("hasLink"):
        return True
    # If none of the unavailable keywords matched, assume available
    return True


async def main() -> int:
    ntfy_topic = os.environ.get("NTFY_TOPIC", "")
    now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    print(f"[{now_str}] Checking Alhambra tickets...")

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
            await page.wait_for_timeout(3000)

            print(f"  Title: {await page.title()!r}")
            await dismiss_cookie_popup(page)

            # Scroll down so the calendar renders
            await page.evaluate("window.scrollTo(0, 600)")
            await page.wait_for_timeout(2000)
            await page.screenshot(path="step1_loaded.png", full_page=True)
            print("  Screenshot: step1_loaded.png")

            # Dump all calendar cells so we can see the real HTML structure
            cells = await dump_calendar_cells(page)
            print(f"  Found {len(cells)} calendar cells")
            if cells:
                print("  Sample cells (first 10):")
                for c in cells[:10]:
                    print(f"    {c}")

            # Save full cell dump for debugging
            with open("calendar_cells.json", "w") as f:
                json.dump(cells, f, indent=2)
            print("  Full cell dump: calendar_cells.json")

            # Check availability for target days in June
            target_day_strs = {str(d) for d in TARGET_DAYS}
            available_days = []

            for cell in cells:
                text = cell.get("text", "").strip()
                if text not in target_day_strs:
                    continue

                # Filter to June 2026 if data-date is present
                data_date = cell.get("dataDate") or ""
                if data_date:
                    if f"{TARGET_YEAR}-{TARGET_MONTH:02d}" not in data_date:
                        continue

                day = int(text)
                available = is_available(cell)
                status = "AVAILABLE" if available else "not available"
                print(f"  June {day}: {status} | class={cell.get('className')!r} | dataDate={data_date!r}")
                if available and day not in available_days:
                    available_days.append(day)

            await page.screenshot(path="step2_result.png", full_page=True)
            print("  Screenshot: step2_result.png")

        except PlaywrightTimeout as e:
            print(f"TIMEOUT: {e}", file=sys.stderr)
            try:
                await page.screenshot(path="error.png", full_page=True)
            except Exception:
                pass
            await browser.close()
            return 1
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            try:
                await page.screenshot(path="error.png", full_page=True)
            except Exception:
                pass
            await browser.close()
            return 1

        await browser.close()

    if available_days:
        days_str = " and ".join(f"June {d}" for d in sorted(available_days))
        print(f"\nTICKETS AVAILABLE: {days_str}!")
        if ntfy_topic:
            send_ntfy(ntfy_topic, sorted(available_days))
        else:
            print("NTFY_TOPIC not set — skipping push notification")
    else:
        print("\nNo tickets available for June 7-8 right now.")

    return 0


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


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
