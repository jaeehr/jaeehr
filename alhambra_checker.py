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

UNAVAILABLE_KEYWORDS = {
    "not-available", "notavailable", "not_available",
    "sold-out", "soldout", "sold_out",
    "disabled", "unavailable", "blocked", "closed",
    "unselectable", "ui-state-disabled", "ui-datepicker-unselectable",
    "no-disponible", "agotado",
}


async def dismiss_cookie_popup(page) -> None:
    # Prefer "Accept" — "Reject" causes a page navigation that breaks the script
    selectors = [
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
                # Wait for any navigation triggered by the cookie choice to finish
                await page.wait_for_load_state("domcontentloaded", timeout=15000)
                await page.wait_for_timeout(2000)
                return
        except Exception:
            pass
    print("  No cookie popup found (or already dismissed)")


async def dump_calendar_cells(page) -> list[dict]:
    """Use JS to extract all calendar date cells — works for both table and div layouts."""
    return await page.evaluate("""
        () => {
            // 1. Prefer elements with data-date attribute (most reliable)
            let cells = Array.from(document.querySelectorAll('[data-date]'));

            // 2. Try common calendar containers
            if (cells.length === 0) {
                const containerSelectors = [
                    '.booking-calendar', '.datepicker', '.calendar',
                    '[class*="calendar"]', '[class*="datepicker"]',
                    '[class*="booking"]',
                ];
                for (const sel of containerSelectors) {
                    const container = document.querySelector(sel);
                    if (container) {
                        const candidates = container.querySelectorAll('td, div, span, a, li');
                        const dayEls = Array.from(candidates).filter(el => {
                            const t = el.textContent.trim();
                            return /^\\d{1,2}$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 31;
                        });
                        if (dayEls.length >= 20) { cells = dayEls; break; }
                    }
                }
            }

            // 3. Broadest fallback: any element whose sole text is 1-31
            if (cells.length === 0) {
                const all = document.querySelectorAll('td, div, span, a, li, button');
                cells = Array.from(all).filter(el => {
                    const t = el.textContent.trim();
                    return /^\\d{1,2}$/.test(t) && parseInt(t) >= 1 && parseInt(t) <= 31;
                });
            }

            return cells.map(el => ({
                tag: el.tagName,
                text: el.textContent.trim(),
                className: el.className,
                dataDate: el.getAttribute('data-date'),
                ariaDisabled: el.getAttribute('aria-disabled'),
                ariaLabel: el.getAttribute('aria-label'),
                title: el.getAttribute('title'),
                hasLink: el.tagName !== 'A' && el.querySelector('a') !== null,
                isLink: el.tagName === 'A',
            }));
        }
    """)


def is_available(cell: dict) -> bool:
    class_lower = (cell.get("className") or "").lower()
    aria_disabled = (cell.get("ariaDisabled") or "").lower()
    if aria_disabled == "true":
        return False
    if any(kw in class_lower for kw in UNAVAILABLE_KEYWORDS):
        return False
    if cell.get("hasLink") or cell.get("isLink"):
        return True
    return True


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
            await page.wait_for_timeout(3000)
            print(f"  Title: {await page.title()!r}")

            await dismiss_cookie_popup(page)

            # Scroll incrementally to trigger lazy-loaded calendar
            for scroll_y in [300, 600, 900, 1200]:
                await page.evaluate(f"window.scrollTo(0, {scroll_y})")
                await page.wait_for_timeout(500)
            await page.wait_for_timeout(2000)

            await page.screenshot(path="step1_loaded.png", full_page=True)
            print("  Screenshot: step1_loaded.png")

            cells = await dump_calendar_cells(page)
            print(f"  Found {len(cells)} calendar cells")
            if cells:
                print("  Sample cells (first 15):")
                for c in cells[:15]:
                    print(f"    {c}")

            with open("calendar_cells.json", "w") as f:
                json.dump(cells, f, indent=2)
            print("  Full cell dump saved: calendar_cells.json")

            target_day_strs = {str(d) for d in TARGET_DAYS}
            available_days = []

            for cell in cells:
                text = cell.get("text", "").strip()
                if text not in target_day_strs:
                    continue
                data_date = cell.get("dataDate") or ""
                if data_date and f"{TARGET_YEAR}-{TARGET_MONTH:02d}" not in data_date:
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
    req = urllib.request.Request(
        f"https://ntfy.sh/{topic}",
        data=f"Tickets for {days_str}, 2026 are available. Book now!".encode(),
        headers={
            "Title": "Alhambra tickets available!",
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
