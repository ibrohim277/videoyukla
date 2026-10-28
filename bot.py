import asyncio
import glob
import logging
import os
import re
import shutil
import tempfile

import yt_dlp
from aiogram import Bot, Dispatcher, F
from aiogram.filters import CommandStart
from aiogram.types import (CallbackQuery, FSInputFile, InlineKeyboardButton,
                           InlineKeyboardMarkup, Message)

# ====== SOZLAMA ======
BOT_TOKEN = "8930202774:AAEW421G0msfltxBUO-bo6nXrJx6C08uyAI"   # @BotFather dan olingan token
RESULTS_PER_PAGE = 10      # bir sahifada nechta qo'shiq
TOTAL_RESULTS = 30         # jami nechta qo'shiq qidiriladi
MAX_DURATION = 900         # soniya (15 daqiqadan uzun treklar o'tkazib yuboriladi)
QUALITIES = [360, 480, 720, 1080]
TG_LIMIT = 49 * 1024 * 1024  # Telegram bot uchun ~50 MB limit
# =====================

logging.basicConfig(level=logging.INFO)
bot = Bot(BOT_TOKEN)
dp = Dispatcher()

HAS_FFMPEG = shutil.which("ffmpeg") is not None
URL_RE = re.compile(r"https?://\S+")

# user_id -> {"query","items"} yoki {"url","title"}
CACHE: dict[int, dict] = {}


# ---------------- QO'SHIQ QIDIRISH ----------------
def search_songs(query: str) -> list[dict]:
    opts = {"quiet": True, "extract_flat": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(f"ytsearch{TOTAL_RESULTS}:{query} audio", download=False)
    items = []
    for e in info.get("entries", []):
        dur = e.get("duration") or 0
        if dur and dur > MAX_DURATION:
            continue
        items.append({"id": e["id"], "title": e.get("title", "Noma'lum"), "duration": dur})
    return items


def download_audio(url: str, folder: str) -> str:
    opts = {
        "format": "bestaudio[ext=m4a]/bestaudio/best",
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return biggest_file(folder)


# ---------------- VIDEO ----------------
def get_info(url: str) -> dict:
    opts = {"quiet": True, "noplaylist": True, "skip_download": True}
    with yt_dlp.YoutubeDL(opts) as ydl:
        return ydl.extract_info(url, download=False)


def video_format(q) -> str:
    if q == "best":
        return "bestvideo+bestaudio/best" if HAS_FFMPEG else "best[ext=mp4]/best"
    q = int(q)
    if HAS_FFMPEG:
        return (f"bestvideo[height<={q}][ext=mp4]+bestaudio[ext=m4a]/"
                f"bestvideo[height<={q}]+bestaudio/best[height<={q}]/best")
    return f"best[height<={q}][ext=mp4]/best[height<={q}]/best"


def download_video(url: str, q, folder: str) -> str:
    opts = {
        "format": video_format(q),
        "outtmpl": os.path.join(folder, "%(id)s.%(ext)s"),
        "quiet": True,
        "noplaylist": True,
        "merge_output_format": "mp4",
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return biggest_file(folder)


def biggest_file(folder: str) -> str:
    files = [f for f in glob.glob(os.path.join(folder, "*")) if os.path.isfile(f)]
    if not files:
        raise RuntimeError("fayl topilmadi")
    return max(files, key=os.path.getsize)


# ---------------- TUGMALAR ----------------
def fmt_time(sec: int) -> str:
    return f"{sec // 60}:{sec % 60:02d}" if sec else "--:--"


def page_markup(items: list[dict], page: int) -> InlineKeyboardMarkup:
    start = page * RESULTS_PER_PAGE
    chunk = items[start:start + RESULTS_PER_PAGE]
    rows = []
    for i, it in enumerate(chunk, start=start):
        rows.append([InlineKeyboardButton(
            text=f"🎵 {it['title'][:45]} ({fmt_time(it['duration'])})", callback_data=f"dl:{i}")])
    nav = []
    if page > 0:
        nav.append(InlineKeyboardButton(text="⬅️ Oldingi", callback_data=f"pg:{page-1}"))
    if start + RESULTS_PER_PAGE < len(items):
        nav.append(InlineKeyboardButton(text="Keyingi ➡️", callback_data=f"pg:{page+1}"))
    if nav:
        rows.append(nav)
    return InlineKeyboardMarkup(inline_keyboard=rows)


def quality_markup(info: dict) -> InlineKeyboardMarkup:
    heights = {f.get("height") for f in info.get("formats", [])
               if f.get("height") and f.get("vcodec") not in (None, "none")}
    max_h = max(heights) if heights else 0
    rows = []
    if len(heights) >= 2:
        btns = [InlineKeyboardButton(text=f"📹 {q}p", callback_data=f"vq:{q}")
                for q in QUALITIES if q <= max_h + 40]
        for i in range(0, len(btns), 2):
            rows.append(btns[i:i + 2])
    else:
        rows.append([InlineKeyboardButton(text="📥 Videoni yuklash", callback_data="vq:best")])
    rows.append([InlineKeyboardButton(text="🎵 Faqat audio", callback_data="vq:audio")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


# ---------------- HANDLERLAR ----------------
@dp.message(CommandStart())
async def start(m: Message):
    await m.answer(
        "🎧 Salom! Men musiqa va video botman.\n\n"
        "🎤 Xonanda ismi yoki qo'shiq nomini yozing — qo'shiqlarini topib beraman.\n"
        "📥 Instagram / YouTube / TikTok havolasini yuboring — videoni "
        "tanlagan sifatda (720p, 1080p...) yuklab beraman.\n\n"
        "Masalan: <b>Shahzoda</b> yoki <b>Ozodbek Nazarbekov</b>",
        parse_mode="HTML")


@dp.message(F.text & ~F.text.startswith("/"))
async def on_text(m: Message):
    text = m.text.strip()
    link = URL_RE.search(text)
    if link:
        await handle_link(m, link.group(0))
    else:
        await handle_search(m, re.sub(r"\s+", " ", text))


async def handle_search(m: Message, query: str):
    wait = await m.answer("🔎 Qidiryapman...")
    try:
        items = await asyncio.to_thread(search_songs, query)
    except Exception:
        logging.exception("search error")
        await wait.edit_text("❌ Qidirishda xatolik. Keyinroq urinib ko'ring.")
        return
    if not items:
        await wait.edit_text("😕 Hech narsa topilmadi. Ismni boshqacha yozib ko'ring.")
        return
    CACHE[m.from_user.id] = {"query": query, "items": items}
    await wait.edit_text(f"🎤 <b>{query}</b> bo'yicha {len(items)} ta natija:",
                         parse_mode="HTML", reply_markup=page_markup(items, 0))


async def handle_link(m: Message, url: str):
    wait = await m.answer("🔗 Havola tekshirilmoqda...")
    try:
        info = await asyncio.to_thread(get_info, url)
    except Exception:
        logging.exception("info error")
        await wait.edit_text(
            "❌ Havolani o'qib bo'lmadi. Hisob yopiq (private) bo'lishi yoki "
            "havola noto'g'ri bo'lishi mumkin.")
        return
    title = (info.get("title") or "Video")[:80]
    CACHE[m.from_user.id] = {"url": url, "title": title}
    await wait.edit_text(f"🎬 <b>{title}</b>\n\nSifatni tanlang:",
                         parse_mode="HTML", reply_markup=quality_markup(info))


@dp.callback_query(F.data.startswith("pg:"))
async def on_page(c: CallbackQuery):
    data = CACHE.get(c.from_user.id)
    if not data or "items" not in data:
        await c.answer("Qaytadan qidiring", show_alert=True)
        return
    page = int(c.data.split(":")[1])
    await c.message.edit_reply_markup(reply_markup=page_markup(data["items"], page))
    await c.answer()


@dp.callback_query(F.data.startswith("dl:"))
async def on_song(c: CallbackQuery):
    data = CACHE.get(c.from_user.id)
    if not data or "items" not in data:
        await c.answer("Qaytadan qidiring", show_alert=True)
        return
    item = data["items"][int(c.data.split(":")[1])]
    await c.answer("⏳ Yuklanmoqda...")
    status = await c.message.answer(f"⏳ Yuklanmoqda: {item['title'][:60]}")
    url = f"https://www.youtube.com/watch?v={item['id']}"
    with tempfile.TemporaryDirectory() as tmp:
        try:
            path = await asyncio.to_thread(download_audio, url, tmp)
            if os.path.getsize(path) > TG_LIMIT:
                await status.edit_text("❌ Fayl juda katta (50 MB dan oshdi).")
                return
            await c.message.answer_audio(
                FSInputFile(path), title=item["title"][:60],
                duration=item["duration"] or None,
                caption="🎧 @" + (await bot.me()).username)
            await status.delete()
        except Exception:
            logging.exception("song error")
            await status.edit_text("❌ Yuklab bo'lmadi. Boshqasini tanlang.")


@dp.callback_query(F.data.startswith("vq:"))
async def on_video(c: CallbackQuery):
    data = CACHE.get(c.from_user.id)
    if not data or "url" not in data:
        await c.answer("Havolani qaytadan yuboring", show_alert=True)
        return
    q = c.data.split(":")[1]
    await c.answer("⏳ Yuklanmoqda...")
    status = await c.message.answer("⏳ Yuklanmoqda, biroz kuting...")
    with tempfile.TemporaryDirectory() as tmp:
        try:
            if q == "audio":
                path = await asyncio.to_thread(download_audio, data["url"], tmp)
            else:
                path = await asyncio.to_thread(download_video, data["url"], q, tmp)
            if os.path.getsize(path) > TG_LIMIT:
                await status.edit_text(
                    "❌ Fayl 50 MB dan katta, Telegram bot yubora olmaydi.\n"
                    "Pastroq sifatni tanlang (masalan 480p yoki 360p).")
                return
            await status.edit_text("📤 Yuborilmoqda...")
            caption = "✅ @" + (await bot.me()).username
            if q == "audio":
                await c.message.answer_audio(FSInputFile(path), title=data["title"], caption=caption)
            else:
                await c.message.answer_video(
                    FSInputFile(path), caption=caption, supports_streaming=True)
            await status.delete()
        except Exception:
            logging.exception("video error")
            await status.edit_text("❌ Yuklab bo'lmadi. Boshqa sifatni sinab ko'ring.")


async def main():
    if not HAS_FFMPEG:
        logging.warning("ffmpeg topilmadi: 1080p YouTube videolar uchun ffmpeg kerak.")
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())