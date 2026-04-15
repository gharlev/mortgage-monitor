#!/usr/bin/env python3
"""
scan_standalone.py — סריקת פוסטים על משכנתא
גרסה עצמאית לריצה ב-GitHub Actions (ללא CDP, עם headless Chromium)
אימות פייסבוק דרך Playwright storageState (FB_STORAGE_STATE)

שינויים:
- הוסרה תלות ב-OpenAI (ללא סיכום AI)
- מניעת כפילויות: seen_posts.json נשמר בין ריצות דרך GitHub Actions cache
- התראה על cookies פגים: שליחת הודעת WhatsApp אם הדפדפן מנותב ל-login
- שימוש ב-Playwright storageState במקום הזרקת cookies ידנית
"""

import asyncio
import json
import hashlib
import os
import string
import requests
from datetime import datetime
from pathlib import Path
from playwright.async_api import async_playwright

# ===== הגדרות מ-Environment Variables =====
GREEN_API_INSTANCE = os.environ.get("GREEN_API_INSTANCE", "7103518794")
GREEN_API_TOKEN = os.environ.get("GREEN_API_TOKEN", "")
WHATSAPP_PHONE = os.environ.get("WHATSAPP_PHONE", "972543339066")
FB_STORAGE_STATE = os.environ.get("FB_STORAGE_STATE", "")  # Playwright storageState JSON
FB_COOKIES_JSON = os.environ.get("FB_COOKIES_JSON", "[]")  # fallback: Cookie-Editor format
IG_COOKIES_JSON = os.environ.get("IG_COOKIES_JSON", "[]")
GH_PAT = os.environ.get("GH_PAT", "")
GH_REPO = os.environ.get("GITHUB_REPOSITORY", "gharlev/mortgage-monitor")

# קבצי state — ב-GitHub Actions נשמרים ב-/tmp (מועברים בין ריצות דרך cache)
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
    """טוען את רשימת הפוסטים שכבר נשלחו — מניעת כפילויות בין ריצות"""
    if SEEN_POSTS_FILE.exists():
        with open(SEEN_POSTS_FILE) as f:
            data = json.load(f)
            seen = set(data) if isinstance(data, list) else set()
            print(f"📋 נטענו {len(seen)} פוסטים ידועים (מניעת כפילויות)")
            return seen
    print("📋 אין היסטוריית פוסטים — ריצה ראשונה")
    return set()


def save_seen_posts(seen):
    """שומר את רשימת הפוסטים שנשלחו — יועבר לריצה הבאה דרך cache"""
    with open(SEEN_POSTS_FILE, 'w') as f:
        json.dump(list(seen), f, ensure_ascii=False)
    print(f"💾 נשמרו {len(seen)} פוסטים ידועים")


def load_pending():
    if PENDING_FILE.exists():
        with open(PENDING_FILE, 'r', encoding='utf-8') as f:
            return json.load(f)
    return {}


def save_pending(pending):
    with open(PENDING_FILE, 'w', encoding='utf-8') as f:
        json.dump(pending, f, ensure_ascii=False, indent=2)


def add_to_pending(post_id, short_id, post_text, post_url, source):
    pending = load_pending()
    pending[post_id] = {
        'post_id': post_id,
        'short_id': short_id,
        'post_text': post_text[:2000],
        'post_url': post_url,
        'source': source,
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


def update_github_secret(secret_name, secret_value):
    """מעדכן GitHub Secret דרך ה-API — לשמירת storageState מעודכן"""
    if not GH_PAT:
        print(f"  ⚠️ GH_PAT לא מוגדר — לא ניתן לעדכן {secret_name}")
        return False
    try:
        import base64
        from nacl import encoding, public as nacl_public

        headers = {
            "Authorization": f"token {GH_PAT}",
            "Accept": "application/vnd.github.v3+json"
        }
        # קבלת מפתח ציבורי של ה-repo
        key_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/public-key"
        r = requests.get(key_url, headers=headers, timeout=10)
        r.raise_for_status()
        key_data = r.json()

        # הצפנה
        pub_key = nacl_public.PublicKey(key_data["key"].encode("utf-8"), encoding.Base64Encoder())
        sealed_box = nacl_public.SealedBox(pub_key)
        encrypted = sealed_box.encrypt(secret_value.encode("utf-8"))
        encrypted_b64 = base64.b64encode(encrypted).decode("utf-8")

        # עדכון ה-secret
        secret_url = f"https://api.github.com/repos/{GH_REPO}/actions/secrets/{secret_name}"
        payload = {"encrypted_value": encrypted_b64, "key_id": key_data["key_id"]}
        r = requests.put(secret_url, headers=headers, json=payload, timeout=10)
        if r.status_code in (201, 204):
            print(f"  ✅ {secret_name} עודכן בהצלחה")
            return True
        else:
            print(f"  ❌ שגיאה בעדכון {secret_name}: {r.status_code}")
            return False
    except Exception as e:
        print(f"  ❌ שגיאה בעדכון GitHub Secret: {e}")
        return False


def build_fb_storage_state():
    """בונה Playwright storageState מ-FB_STORAGE_STATE או מ-FB_COOKIES_JSON כ-fallback"""
    # נסה קודם FB_STORAGE_STATE (פורמט Playwright מלא)
    if FB_STORAGE_STATE:
        try:
            state = json.loads(FB_STORAGE_STATE)
            if "cookies" in state:
                print(f"✅ נטען FB_STORAGE_STATE עם {len(state['cookies'])} cookies")
                return state
        except Exception as e:
            print(f"⚠️ שגיאה בפענוח FB_STORAGE_STATE: {e}")

    # fallback: FB_COOKIES_JSON (פורמט Cookie-Editor)
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

    print("⚠️ אין FB cookies זמינים")
    return None


def clean_ig_cookies(cookies_list):
    """ניקוי IG cookies לפורמט תקין של Playwright"""
    valid_samesite = {'Strict', 'Lax', 'None'}
    ss_map = {'lax': 'Lax', 'strict': 'Strict', 'none': 'None', 'no_restriction': 'None'}
    cleaned = []
    for c in cookies_list:
        ss = c.get('sameSite') or c.get('samesite')
        if isinstance(ss, str):
            ss = ss_map.get(ss.lower(), ss)
            if ss not in valid_samesite:
                ss = 'None'
        else:
            ss = 'None'
        cookie = {
            'name': c.get('name', ''),
            'value': c.get('value', ''),
            'domain': c.get('domain', '.instagram.com'),
            'path': c.get('path', '/'),
            'sameSite': ss,
        }
        if c.get('secure') is not None:
            cookie['secure'] = bool(c['secure'])
        if c.get('httpOnly') is not None:
            cookie['httpOnly'] = bool(c['httpOnly'])
        if c.get('expirationDate'):
            cookie['expires'] = int(c['expirationDate'])
        cleaned.append(cookie)
    return cleaned


async def scrape_facebook_group(page, group_url, seen_posts):
    new_posts = []
    group_name = group_url.split('/')[-1]
    try:
        print(f"  📘 סורק: {group_name}")
        await page.goto(group_url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)

        # בדיקת cookies פגים — אם מנותב ל-login
        current_url = page.url
        if 'login' in current_url or 'checkpoint' in current_url:
            print(f"    ⚠️ cookies פייסבוק פגו — נשלחת התראה לוואטסאפ")
            send_whatsapp(
                "⚠️ *התראת מערכת — Mortgage Monitor*\n\n"
                "ה-cookies של פייסבוק פגו ואינם תקפים יותר.\n"
                "הסריקה לא יכולה להתחבר לקבוצות.\n\n"
                "נדרש חידוש cookies — אנא פנה ל-Manus לביצוע החידוש."
            )
            return new_posts, True  # True = cookies פגו

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
    return new_posts, False  # False = cookies תקינים


async def scrape_instagram(page, hashtag, seen_posts):
    new_posts = []
    cookies_expired = False
    try:
        print(f"  📸 סורק אינסטגרם: #{hashtag}")
        url = f"https://www.instagram.com/explore/tags/{hashtag}/"
        await page.goto(url, wait_until='domcontentloaded', timeout=30000)
        await asyncio.sleep(4)

        # בדיקת cookies פגים
        current_url = page.url
        if 'login' in current_url or 'accounts' in current_url:
            print(f"    ⚠️ cookies אינסטגרם פגו — נשלחת התראה לוואטסאפ")
            send_whatsapp(
                "⚠️ *התראת מערכת — Mortgage Monitor*\n\n"
                "ה-cookies של אינסטגרם פגו ואינם תקפים יותר.\n"
                "הסריקה לא יכולה להתחבר לאינסטגרם.\n\n"
                "נדרש חידוש cookies — אנא פנה ל-Manus לביצוע החידוש."
            )
            return new_posts, True  # True = cookies פגו

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
    return new_posts, cookies_expired


async def run_scan():
    print("=" * 50)
    print(f"🔍 מתחיל סריקה - {datetime.now().strftime('%d/%m/%Y %H:%M')}")
    print("=" * 50)

    # בניית FB storageState
    fb_storage_state = build_fb_storage_state()

    # טעינת IG cookies
    try:
        ig_cookies = clean_ig_cookies(json.loads(IG_COOKIES_JSON))
        print(f"✅ נטענו {len(ig_cookies)} IG cookies")
    except Exception as e:
        print(f"❌ שגיאה בטעינת IG cookies: {e}")
        ig_cookies = []

    # טעינת היסטוריית פוסטים (מניעת כפילויות)
    seen_posts = load_seen_posts()
    all_new_posts = []
    fb_cookies_expired = False
    ig_cookies_expired = False

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

        # יצירת context עם FB storageState (Playwright native)
        fb_context_kwargs = {
            'viewport': {'width': 1280, 'height': 900},
            'locale': 'he-IL',
        }
        if fb_storage_state:
            fb_context_kwargs['storage_state'] = fb_storage_state

        fb_context = await browser.new_context(**fb_context_kwargs)
        fb_page = await fb_context.new_page()

        # סריקת קבוצות פייסבוק
        print("\n📘 סורק קבוצות פייסבוק...")
        for group_url in FACEBOOK_GROUPS:
            posts, expired = await scrape_facebook_group(fb_page, group_url, seen_posts)
            if expired:
                fb_cookies_expired = True
                break  # אין טעם להמשיך אם cookies פגו
            all_new_posts.extend(posts)
            for p_item in posts:
                seen_posts.add(p_item['id'])

        # שמירת storageState מעודכן אחרי הסריקה (לחידוש cookies)
        if not fb_cookies_expired and GH_PAT:
            try:
                updated_state = await fb_context.storage_state()
                updated_state_json = json.dumps(updated_state, ensure_ascii=False)
                print("\n💾 שומר FB storageState מעודכן...")
                update_github_secret("FB_STORAGE_STATE", updated_state_json)
            except Exception as e:
                print(f"  ⚠️ לא ניתן לשמור storageState: {e}")

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
        ig_posts, ig_expired = await scrape_instagram(ig_page, INSTAGRAM_HASHTAG, seen_posts)
        if ig_expired:
            ig_cookies_expired = True
        else:
            all_new_posts.extend(ig_posts)
            for p_item in ig_posts:
                seen_posts.add(p_item['id'])

        await ig_context.close()
        await browser.close()

    print(f"\n📊 סה\"כ {len(all_new_posts)} פוסטים חדשים")

    # שמירת היסטוריית פוסטים (גם אם אין פוסטים חדשים — לשמור את הנוכחיים)
    save_seen_posts(seen_posts)

    if not all_new_posts:
        if not fb_cookies_expired and not ig_cookies_expired:
            print("אין פוסטים חדשים")
        return

    # בניית הודעת WhatsApp ללא סיכום AI — הטקסט הגולמי + קישור
    now_str = datetime.now().strftime('%d/%m %H:%M')
    msg_lines = [f"🔍 סריקה {now_str} — {len(all_new_posts)} פוסטים חדשים:\n"]

    for post in all_new_posts:
        short_id = generate_short_id(post['id'])
        add_to_pending(post['id'], short_id, post['text'], post['url'], post['source'])

        # תקציר קצר: 150 תווים ראשונים מהטקסט
        preview = post['text'][:150].replace('\n', ' ').strip()
        if len(post['text']) > 150:
            preview += "..."

        msg_lines.append(
            f"[{short_id}] {post['source']}\n"
            f"{preview}\n"
            f"{post['url']}\n"
        )

    full_msg = "\n".join(msg_lines)

    # WhatsApp מגביל ל-4000 תווים — אם ארוך מדי, שלח בחלקים
    print(f"\n📱 שולח לוואטסאפ...")
    if len(full_msg) <= 4000:
        send_whatsapp(full_msg)
    else:
        # שלח בחלקים
        chunk = msg_lines[0]  # כותרת
        for line in msg_lines[1:]:
            if len(chunk) + len(line) + 1 > 3800:
                send_whatsapp(chunk)
                chunk = f"🔍 המשך סריקה {now_str}:\n\n" + line
            else:
                chunk += "\n" + line
        if chunk:
            send_whatsapp(chunk)


if __name__ == "__main__":
    asyncio.run(run_scan())
