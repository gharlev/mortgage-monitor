#!/usr/bin/env python3
"""
publish_standalone.py — פרסום פוסטים לקבוצות פייסבוק ולאינסטגרם
גרסה עצמאית לריצה ב-GitHub Actions (ללא CDP, עם headless Chromium)
cookies נטענות מ-environment variables
"""

import asyncio
import json
import os
import requests
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

# ===== הגדרות מ-Environment Variables =====
GREEN_API_INSTANCE = os.environ.get("GREEN_API_INSTANCE", "7103518794")
GREEN_API_TOKEN = os.environ.get("GREEN_API_TOKEN", "")
WHATSAPP_PHONE = os.environ.get("WHATSAPP_PHONE", "972543339066")
FB_STORAGE_STATE = os.environ.get("FB_STORAGE_STATE", "")
FB_COOKIES_JSON = os.environ.get("FB_COOKIES_JSON", "[]")
IG_COOKIES_JSON = os.environ.get("IG_COOKIES_JSON", "[]")

# קבצי state — נטענים מ-environment variable (JSON מקודד)
POSTS_DATA_JSON = os.environ.get("POSTS_DATA_JSON", "[]")
PUBLISH_STATE_JSON = os.environ.get("PUBLISH_STATE_JSON", '{"next_post_index": 0, "published_count": 0}')

# תיקיית output לשמירת state מעודכן
BASE_DIR = Path(os.environ.get("DATA_DIR", "/tmp/mortgage_data"))
BASE_DIR.mkdir(parents=True, exist_ok=True)

FACEBOOK_GROUPS = [
    {"name": "פורום משכנתאות", "url": "https://www.facebook.com/groups/httpspln.co.il"},
    {"name": "קבוצת משכנתאות 626396", "url": "https://www.facebook.com/groups/626396175276195"},
    {"name": "משכנתא זקן", "url": "https://www.facebook.com/groups/mashkantazekan"},
    {"name": "קבוצת משכנתאות 497197", "url": "https://www.facebook.com/groups/497197171420020"},
    {"name": "Simple House", "url": "https://www.facebook.com/groups/simplehouseil"},
    {"name": "קבוצת משכנתאות 333238", "url": "https://www.facebook.com/groups/333238607094709"},
]


def send_whatsapp(message: str) -> bool:
    url = f"https://api.green-api.com/waInstance{GREEN_API_INSTANCE}/sendMessage/{GREEN_API_TOKEN}"
    payload = {"chatId": f"{WHATSAPP_PHONE}@c.us", "message": message}
    try:
        r = requests.post(url, json=payload, timeout=15)
        return r.status_code == 200
    except Exception as e:
        print(f"שגיאה בשליחת וואטסאפ: {e}")
        return False


async def publish_to_fb_group(page, group_url: str, group_name: str, post_text: str, post_index: int):
    try:
        print(f"  📘 מנווט לקבוצה: {group_name}")
        await page.goto(group_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)

        if 'login' in page.url:
            print(f"  ⚠️ לא מחובר לפייסבוק — cookies פגו?")
            return None

        # חיפוש תיבת כתיבה
        textbox = None
        try:
            all_btns = await page.query_selector_all('div[role="button"]')
            for el in all_btns:
                txt = await el.inner_text()
                if 'כאן כותבים' in txt or 'כתוב פוסט' in txt or 'מה בראשך' in txt:
                    textbox = el
                    break
        except Exception:
            pass

        if not textbox:
            for sel in ['[aria-label="כתוב פוסט..."]', '[aria-label="מה בראשך?"]', '[aria-label="כתוב משהו..."]']:
                try:
                    elements = await page.query_selector_all(sel)
                    if elements:
                        textbox = elements[0]
                        break
                except Exception:
                    continue

        if not textbox:
            print(f"  ⚠️ לא נמצאה תיבת כתיבה בקבוצה {group_name}")
            return None

        await textbox.click()
        await asyncio.sleep(2)

        dialog_textbox = await page.query_selector('[aria-placeholder="יצירת פוסט ציבורי..."]')
        if dialog_textbox:
            await dialog_textbox.click()
            await asyncio.sleep(0.5)

        await page.keyboard.type(post_text, delay=0)
        await asyncio.sleep(1)

        # לחיצה על פרסום
        publish_btn = await page.query_selector('[aria-label="פרסום"][role="button"]')
        if not publish_btn:
            publish_btn = await page.query_selector('[aria-label="פרסום"]')
        if not publish_btn:
            dialogs = await page.query_selector_all('[role="dialog"]')
            for dialog in dialogs:
                btns = await dialog.query_selector_all('[role="button"]')
                for btn in btns:
                    txt = await btn.inner_text()
                    if txt.strip() == 'פרסום':
                        publish_btn = btn
                        break
                if publish_btn:
                    break

        if not publish_btn:
            print(f"  ⚠️ לא נמצא כפתור פרסום בקבוצה {group_name}")
            return None

        await publish_btn.click()
        await asyncio.sleep(5)

        current_url = page.url
        print(f"  ✅ פורסם בקבוצה {group_name}")
        return current_url

    except Exception as e:
        print(f"  ❌ שגיאה בקבוצה {group_name}: {e}")
        return None


async def publish_to_instagram(page, post_text: str, post_index: int):
    try:
        print('  📸 מפרסם לאינסטגרם דרך Meta Business Suite...')
        ig_composer_url = (
            'https://business.facebook.com/latest/composer/'
            '?asset_id=218213748926589'
            '&context_ref=HOME'
            '&nav_ref=internal_nav'
            '&ref=biz_web_home_create_post'
        )
        await page.goto(ig_composer_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(5)

        if 'login' in page.url:
            print('  ⚠️ לא מחובר — cookies פגו?')
            return None

        try:
            ig_checkbox = await page.wait_for_selector('[aria-label="Instagram"]', timeout=5000)
            if ig_checkbox:
                is_checked = await ig_checkbox.is_checked()
                if not is_checked:
                    await ig_checkbox.click()
                    await asyncio.sleep(1)
        except Exception:
            pass

        textbox = await page.wait_for_selector(
            '[aria-label="כתוב בתיבת הדיאלוג כדי להוסיף טקסט לפוסט."]',
            timeout=10000
        )
        await textbox.click()
        await asyncio.sleep(0.5)
        await page.keyboard.type(post_text, delay=0)
        await asyncio.sleep(1)

        await page.mouse.click(672, 847)
        await asyncio.sleep(6)
        print('  ✅ פורסם לאינסטגרם')

        try:
            await page.goto('https://www.instagram.com/gharlev/', wait_until='domcontentloaded', timeout=20000)
            await asyncio.sleep(3)
            first_post = await page.query_selector('article a[href*="/p/"]')
            if first_post:
                href = await first_post.get_attribute('href')
                return f'https://www.instagram.com{href}'
        except Exception:
            pass
        return 'https://www.instagram.com/gharlev/'

    except Exception as e:
        print(f'  ❌ שגיאה בפרסום לאינסטגרם: {e}')
        return None


def clean_cookies(cookies_list):
    """ניקוי cookies לפורמט תקין של Playwright"""
    valid_samesite = {'Strict', 'Lax', 'None'}
    cleaned = []
    for c in cookies_list:
        cookie = {
            'name': c.get('name', ''),
            'value': c.get('value', ''),
            'domain': c.get('domain', '.facebook.com'),
            'path': c.get('path', '/'),
        }
        ss = c.get('sameSite') or c.get('samesite')
        if isinstance(ss, str):
            ss_map = {'lax': 'Lax', 'strict': 'Strict', 'none': 'None', 'no_restriction': 'None'}
            ss = ss_map.get(ss.lower(), ss)
            cookie['sameSite'] = ss if ss in valid_samesite else 'None'
        else:
            cookie['sameSite'] = 'None'
        if c.get('secure') is not None:
            cookie['secure'] = bool(c['secure'])
        if c.get('httpOnly') is not None:
            cookie['httpOnly'] = bool(c['httpOnly'])
        if c.get('expirationDate'):
            cookie['expires'] = int(c['expirationDate'])
        cleaned.append(cookie)
    return cleaned

def build_fb_storage_state():
    """בונה Playwright storageState מ-FB_STORAGE_STATE או מ-FB_COOKIES_JSON כ-fallback"""
    if FB_STORAGE_STATE:
        try:
            state = json.loads(FB_STORAGE_STATE)
            if "cookies" in state:
                print(f"✅ נטען FB_STORAGE_STATE עם {len(state['cookies'])} cookies")
                return state
        except Exception as e:
            print(f"⚠️ שגיאה בפענוח FB_STORAGE_STATE: {e}")
    # fallback: FB_COOKIES_JSON
    if FB_COOKIES_JSON and FB_COOKIES_JSON != "[]":
        try:
            ce_cookies = json.loads(FB_COOKIES_JSON)
            valid_samesite = {'Strict', 'Lax', 'None'}
            ss_map = {'lax': 'Lax', 'strict': 'Strict', 'none': 'None', 'no_restriction': 'None'}
            playwright_cookies = []
            for c in ce_cookies:
                expires = c.get("expirationDate", -1) or -1
                ss = c.get("sameSite") or c.get("samesite")
                if isinstance(ss, str):
                    ss = ss_map.get(ss.lower(), ss)
                    if ss not in valid_samesite:
                        ss = 'None'
                else:
                    ss = 'None'
                playwright_cookies.append({
                    "name": c["name"],
                    "value": c["value"],
                    "domain": c.get("domain", ".facebook.com"),
                    "path": c.get("path", "/"),
                    "expires": int(expires) if expires != -1 else -1,
                    "httpOnly": bool(c.get("httpOnly", False)),
                    "secure": bool(c.get("secure", True)),
                    "sameSite": ss
                })
            print(f"✅ נטענו {len(playwright_cookies)} FB cookies מ-FB_COOKIES_JSON (fallback)")
            return {"cookies": playwright_cookies, "origins": []}
        except Exception as e:
            print(f"⚠️ שגיאה בפענוח FB_COOKIES_JSON: {e}")
    return None

async def main():
    # טעינת נתונים
    try:
        fb_storage_state = build_fb_storage_state()
        ig_cookies = clean_cookies(json.loads(IG_COOKIES_JSON))
        posts = json.loads(POSTS_DATA_JSON)
        state = json.loads(PUBLISH_STATE_JSON)
    except Exception as e:
        print(f"❌ שגיאה בטעינת נתונים: {e}")
        return

    post_index = state.get('next_post_index', 0)

    if post_index >= len(posts):
        print(f"✅ כל {len(posts)} הפוסטים פורסמו כבר!")
        return

    post = posts[post_index]
    print(f"\n📝 מפרסם פוסט {post_index + 1}/{len(posts)}: {post['title']}")

    fb_text = post.get('facebook', post.get('text', ''))
    ig_text = post.get('instagram', fb_text)
    published_urls = []

    async with async_playwright() as p:
        browser = await p.chromium.launch(
            headless=True,
            args=[
                '--no-sandbox',
                '--disable-setuid-sandbox',
                '--disable-dev-shm-usage',
                '--disable-blink-features=AutomationControlled',
                '--user-agent=Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'
            ]
        )

        # Context לפייסבוק — שימוש ב-storage_state (Playwright native)
        fb_context_kwargs = {'viewport': {'width': 1280, 'height': 900}, 'locale': 'he-IL'}
        if fb_storage_state:
            fb_context_kwargs['storage_state'] = fb_storage_state
        fb_context = await browser.new_context(**fb_context_kwargs)
        fb_page = await fb_context.new_page()

        print(f"\n📘 מפרסם ל-{len(FACEBOOK_GROUPS)} קבוצות פייסבוק...")
        for group in FACEBOOK_GROUPS:
            url = await publish_to_fb_group(fb_page, group['url'], group['name'], fb_text, post_index)
            if url:
                published_urls.append({'platform': 'facebook', 'name': group['name'], 'url': url})
            await asyncio.sleep(2)

        await fb_context.close()

        # Context לאינסטגרם (Meta Business Suite — משתמש ב-FB storageState)
        ig_context_kwargs = {'viewport': {'width': 1280, 'height': 900}, 'locale': 'he-IL'}
        if fb_storage_state:
            ig_context_kwargs['storage_state'] = fb_storage_state
        ig_context = await browser.new_context(**ig_context_kwargs)
        ig_page = await ig_context.new_page()

        print('\n📸 מפרסם לאינסטגרם...')
        ig_url = await publish_to_instagram(ig_page, ig_text, post_index)
        if ig_url:
            published_urls.append({'platform': 'instagram', 'name': 'Instagram', 'url': ig_url})

        await ig_context.close()
        await browser.close()

    # עדכון state
    state['next_post_index'] = post_index + 1
    state['published_count'] = state.get('published_count', 0) + 1
    state['last_published'] = {
        'index': post_index,
        'title': post['title'],
        'timestamp': datetime.now().isoformat(),
        'urls': published_urls
    }

    # שמירת state מעודכן לקובץ (ל-artifact ב-GitHub Actions)
    state_file = BASE_DIR / 'publish_state.json'
    with open(state_file, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
    print(f"\n✅ State נשמר: next_post_index={state['next_post_index']}")

    # שליחת קישורים לוואטסאפ
    if published_urls:
        msg = f"✅ פוסט {post_index + 1}/{len(posts)} פורסם!\n"
        msg += f"📝 {post['title']}\n\n🔗 קישורים:\n"
        for p_info in published_urls:
            msg += f"• {p_info['name']}: {p_info['url']}\n"
        send_whatsapp(msg)
        print(f"📱 קישורים נשלחו לוואטסאפ")
    else:
        print("⚠️ לא פורסם בשום פלטפורמה")


if __name__ == "__main__":
    asyncio.run(main())
