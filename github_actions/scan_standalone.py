#!/usr/bin/env python3
"""
scan_standalone.py — סריקת פוסטים על משכנתא
גרסה עצמאית לריצה ב-GitHub Actions (ללא CDP, עם headless Chromium)
cookies נטענות מ-environment variables
"""

import asyncio
import json
import hashlib
import os
import string
import requests
from datetime import datetime
from pathlib import Path
from openai import OpenAI
from playwright.async_api import async_playwright

# ===== הגדרות מ-Environment Variables =====
GREEN_API_INSTANCE = os.environ.get("GREEN_API_INSTANCE", "7103518794")
GREEN_API_TOKEN = os.environ.get("GREEN_API_TOKEN", "")
WHATSAPP_PHONE = os.environ.get("WHATSAPP_PHONE", "972543339066")
OPENAI_API_KEY = os.environ.get("OPENAI_API_KEY", "")
FB_COOKIES_JSON = os.environ.get("FB_COOKIES_JSON", "[]")
IG_COOKIES_JSON = os.environ.get("IG_COOKIES_JSON", "[]")

# קבצי state — ב-GitHub Actions נשמרים ב-/tmp
BASE_DIR = Path(os.environ.get("DATA_DIR", "/tmp/mortgage_data"))
BASE_DIR.mkdir(parents=True, exist_ok=True)
SEEN_POSTS_FILE = BASE_DIR / "seen_posts.json"
PENDING_FILE = BASE_DIR / "pending_responses.json"

FACEBOOK_GROUPS = [
    "https://www.facebook.com/groups/httpspln.co.il",
    "https://www.facebook.com/groups/626396175276195",
    "https://www.facebook.com/groups/mashkantazekan",
    "https://www.facebook.com/groups/497197171420020",
    "https://www.facebook.com/groups/simplehouseil",
    "https://www.facebook.com/groups/333238607094709",
]

INSTAGRAM_HASHTAG = "משכנתא"

MORTGAGE_KEYWORDS = [
    "משכנתא", "ריבית", "הלוואה", "בנק", "מימון", "נדלן", 'נדל"ן',
    "דירה", "רכישה", "פריים", "מסלול", "מחזור", "לווה", "שמאי",
    "קרן", "החזר חודשי", "ליבור", "מדד", "הצמדה", "ריבית קבועה",
    "ריבית משתנה", "תמהיל", "בנק למשכנתאות", "עמלת פירעון"
]

COMMERCIAL_INDICATORS = [
    "צרו קשר", "צור קשר", "פנו אליי", "פנו אלי",
    "השאירו פרטים", "השאר פרטים",
    "לתיאום פגישה", "לתיאום ייעוץ",
    "ייעוץ חינם", "ייעוץ ללא עלות", "ייעוץ ראשוני חינם",
    "מוזמנים לפנות", "השאירו הודעה", "שלחו הודעה",
    "הגיע אליי לקוח", "הגיע אלי לקוח", "הגיעה אליי",
    "הצלחתי לחסוך", "חסכתי ללקוח",
    "יועץ משכנתאות מוסמך", "יועץ משכנתאות מנוסה",
    "מומחה למשכנתאות", "מומחית למשכנתאות",
    "שירותי ייעוץ", "חבילת ייעוץ",
    "מחכה לפניותיכם", "מחכה לפנייתכם",
    "linktr.ee", "bio link", "ליצור קשר",
]

# JavaScript לחילוץ פוסטים מפייסבוק
FB_EXTRACT_JS = """
() => {
    const posts = [];
    const articles = document.querySelectorAll('[role="article"]');
    articles.forEach((article) => {
        const text = article.innerText?.trim();
        if (!text || text.length < 30) return;
        const postLinks = [...article.querySelectorAll('a[href*="/posts/"]')];
        let postUrl = null;
        for (const link of postLinks) {
            const href = link.href;
            if (href && href.includes('/posts/') && !href.includes('/comment')) {
                postUrl = href.split('?')[0];
                break;
            }
        }
        posts.push({ text: text.substring(0, 1500), url: postUrl });
    });
    return posts;
}
"""

IG_EXTRACT_JS = """
() => {
    const links = [...document.querySelectorAll('a[href*="/p/"]')];
    return links.map(l => ({
        url: l.href,
        id: l.href.split('/p/')[1]?.split('/')[0]
    })).filter(l => l.id && l.id.length > 3);
}
"""


def generate_short_id(post_id_str):
    chars = string.ascii_uppercase + string.digits
    hash_val = int(hashlib.md5(post_id_str.encode()).hexdigest(), 16)
    short_id = ''
    for _ in range(3):
        short_id += chars[hash_val % len(chars)]
        hash_val //= len(chars)
    return short_id


def load_seen_posts():
    if SEEN_POSTS_FILE.exists():
        with open(SEEN_POSTS_FILE) as f:
            data = json.load(f)
            return set(data) if isinstance(data, list) else set()
    return set()


def save_seen_posts(seen):
    with open(SEEN_POSTS_FILE, 'w') as f:
        json.dump(list(seen), f, ensure_ascii=False)


def load_pending():
    if PENDING_FILE.exists():
        with open(PENDING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_pending(pending):
    with open(PENDING_FILE, 'w', encoding='utf-8') as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def add_to_pending(post_id, short_id, post_text, post_url, source, summary):
    pending = load_pending()
    pending[post_id] = {
        'post_id': post_id,
        'short_id': short_id,
        'post_text': post_text[:2000],
        'post_url': post_url,
        'source': source,
        'summary': summary,
        'timestamp': datetime.now().isoformat(),
        'status': 'waiting'
    }
    save_pending(pending)


def is_commercial_post(text):
    text_lower = text.lower()
    for indicator in COMMERCIAL_INDICATORS:
        if indicator.lower() in text_lower:
            return True, indicator
    return False, None


def is_mortgage_related(text):
    text_lower = text.lower()
    return any(kw.lower() in text_lower for kw in MORTGAGE_KEYWORDS)


def summarize_post(text):
    try:
        client = OpenAI(api_key=OPENAI_API_KEY)
        response = client.chat.completions.create(
            model="gpt-4.1-mini",
            messages=[
                {"role": "system", "content": "אתה עוזר שמסכם פוסטים על משכנתאות בעברית. סכם בקצרה (2-3 משפטים) את עיקר הפוסט."},
                {"role": "user", "content": f"סכם את הפוסט הבא:\n\n{text[:2000]}"}
            ],
            max_tokens=200
        )
        return response.choices[0].message.content.strip()
    except Exception as e:
        print(f"  שגיאה בסיכום: {e}")
        return text[:200] + "..."


def send_whatsapp(message):
    url = f"https://api.green-api.com/waInstance{GREEN_API_INSTANCE}/sendMessage/{GREEN_API_TOKEN}"
    payload = {"chatId": f"{WHATSAPP_PHONE}@c.us", "message": message}
    try:
        response = requests.post(url, json=payload, timeout=15)
        if response.status_code == 200:
            print(f"  ✅ הודעה נשלחה לוואטסאפ")
            return True
        else:
            print(f"  ❌ שגיאה בוואטסאפ: {response.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ שגיאת חיבור וואטסאפ: {e}")
        return False


async def scrape_facebook_group(page, group_url, seen_posts):
    new_posts = []
    group_name = group_url.split('/')[-1]
    try:
        print(f"  📘 סורק: {group_name}")
        await page.goto(group_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)
        if 'login' in page.url:
            print(f"    ⚠️ לא מחובר — צריך לרענן cookies")
            return new_posts
        await page.evaluate("window.scrollBy(0, 500)")
        await asyncio.sleep(2)
        posts_data = await page.evaluate(FB_EXTRACT_JS)
        print(f"    נמצאו {len(posts_data)} אלמנטים")
        for post in posts_data:
            text = post.get('text', '')
            post_url = post.get('url') or group_url
            if post.get('url'):
                post_id = post['url'].split('?')[0]
            else:
                post_id = f"fb_{group_name}_{abs(hash(text[:100]))}"
            if post_id in seen_posts:
                continue
            if is_mortgage_related(text):
                is_comm, reason = is_commercial_post(text)
                if is_comm:
                    print(f"    ⏭️ סונן (שיווקי): {reason}")
                    continue
                new_posts.append({'id': post_id, 'text': text, 'url': post_url, 'source': f'פייסבוק - {group_name}'})
                print(f"    ✅ פוסט חדש: {text[:80]}...")
        print(f"    {len(new_posts)} פוסטים רלוונטיים חדשים")
    except Exception as e:
        print(f"    ❌ שגיאה: {e}")
    return new_posts


async def scrape_instagram(page, hashtag, seen_posts):
    new_posts = []
    try:
        print(f"  📸 סורק אינסטגרם: #{hashtag}")
        url = f"https://www.instagram.com/explore/tags/{hashtag}/"
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)
        if 'login' in page.url or 'accounts' in page.url:
            print(f"    ⚠️ לא מחובר לאינסטגרם — צריך לרענן cookies")
            return new_posts
        post_links = await page.evaluate(IG_EXTRACT_JS)
        print(f"    נמצאו {len(post_links)} פוסטים")
        for link_data in post_links[:8]:
            post_url = link_data.get('url', '')
            post_id = f"ig_{link_data.get('id', '')}"
            if not post_url or post_id in seen_posts:
                continue
            try:
                post_page = await page.context.new_page()
                await post_page.goto(post_url, wait_until='domcontentloaded', timeout=20000)
                await asyncio.sleep(2)
                text = await post_page.evaluate("""
                    () => {
                        const meta = document.querySelector('meta[name="description"]');
                        if (meta) return meta.content;
                        const article = document.querySelector('article');
                        return article ? article.innerText?.substring(0, 500) : '';
                    }
                """)
                await post_page.close()
                if text and is_mortgage_related(text):
                    is_comm, reason = is_commercial_post(text)
                    if is_comm:
                        print(f"    ⏭️ סונן אינסטגרם (שיווקי): {reason}")
                    else:
                        new_posts.append({'id': post_id, 'text': text, 'url': post_url, 'source': f'אינסטגרם - #{hashtag}'})
                        print(f"    ✅ פוסט אינסטגרם: {text[:80]}...")
            except Exception as e:
                try:
                    await post_page.close()
                except Exception:
                    pass
                continue
        print(f"    {len(new_posts)} פוסטים רלוונטיים חדשים")
    except Exception as e:
        print(f"    ❌ שגיאה: {e}")
    return new_posts


async def run_scan():
    print("=" * 50)
    print(f"🔍 מתחיל סריקה - {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 50)

    # טעינת cookies מ-environment variables
    try:
        fb_cookies = json.loads(FB_COOKIES_JSON)
        ig_cookies = json.loads(IG_COOKIES_JSON)
        print(f"✅ נטענו {len(fb_cookies)} FB cookies, {len(ig_cookies)} IG cookies")
    except Exception as e:
        print(f"❌ שגיאה בטעינת cookies: {e}")
        fb_cookies = []
        ig_cookies = []

    seen_posts = load_seen_posts()
    all_new_posts = []

    async with async_playwright() as p:
        # הפעלת Chromium headless
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

        # יצירת context עם cookies של פייסבוק
        fb_context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='he-IL',
        )
        if fb_cookies:
            await fb_context.add_cookies(fb_cookies)
        fb_page = await fb_context.new_page()

        # סריקת קבוצות פייסבוק
        print("\n📘 סורק קבוצות פייסבוק...")
        for group_url in FACEBOOK_GROUPS:
            posts = await scrape_facebook_group(fb_page, group_url, seen_posts)
            all_new_posts.extend(posts)
            for p_item in posts:
                seen_posts.add(p_item['id'])

        await fb_context.close()

        # יצירת context עם cookies של אינסטגרם
        ig_context = await browser.new_context(
            viewport={'width': 1280, 'height': 900},
            locale='he-IL',
        )
        if ig_cookies:
            await ig_context.add_cookies(ig_cookies)
        ig_page = await ig_context.new_page()

        # סריקת אינסטגרם
        print("\n📸 סורק אינסטגרם...")
        ig_posts = await scrape_instagram(ig_page, INSTAGRAM_HASHTAG, seen_posts)
        all_new_posts.extend(ig_posts)
        for p_item in ig_posts:
            seen_posts.add(p_item['id'])

        await ig_context.close()
        await browser.close()

    print(f"\n📊 סה\"כ {len(all_new_posts)} פוסטים חדשים")
    save_seen_posts(seen_posts)

    if not all_new_posts:
        print("אין פוסטים חדשים")
        return

    # סיכום ושליחה לוואטסאפ
    msg_lines = [f"🔍 סריקה {datetime.now().strftime('%d/%m %H:%M')} — {len(all_new_posts)} פוסטים חדשים:\n"]
    for post in all_new_posts:
        short_id = generate_short_id(post['id'])
        summary = summarize_post(post['text'])
        add_to_pending(post['id'], short_id, post['text'], post['url'], post['source'], summary)
        msg_lines.append(f"[{short_id}] {post['source']}\n{summary}\n{post['url']}\n")

    full_msg = "\n".join(msg_lines)
    print(f"\n📱 שולח לוואטסאפ...")
    send_whatsapp(full_msg[:4000])


if __name__ == "__main__":
    asyncio.run(run_scan())
