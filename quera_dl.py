import base64
import json
import random
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from urllib.parse import urlparse

import requests

BASE = "https://quera.org"
COLLEGE_ID = 6092
START_URL = f"{BASE}/college/{COLLEGE_ID}/chapter/19659/lesson/66001"
OUT = Path("output")  # downloaded content goes here (git-ignored)
INCLUDE_EXERCISES = True   # also try to save PRO (exercise) problems
RETRY_EMPTY = True         # re-request lessons that were empty before
BASE_DELAY = 3.0            # seconds between lesson requests (plus jitter)
MAX_DELAY = 20.0
UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0 Safari/537.36")

NEXT_DATA_RE = re.compile(
    r'<script id="__NEXT_DATA__" type="application/json">(.*?)</script>', re.S
)
MD_IMG_RE = re.compile(r'!\[([^\]]*)\]\(\s*([^)\s]+)(?:\s+"[^"]*")?\s*\)')
HTML_IMG_RE = re.compile(r'(<img\b[^>]*?\bsrc=")([^"]+)(")', re.I)
VIDEO_MARK_RE = re.compile(r'%video:(/video/player/[A-Za-z0-9_\-\.]+)%')
PROJECT_LINK_RE = re.compile(
    r'/college/assignments/\d+/download_problem_initial_project/(\d+)/?'
)

COOKIE = Path("cookies.txt").read_text(encoding="utf-8").strip()
session = requests.Session()
session.headers.update({"User-Agent": UA})

delay = {"current": BASE_DELAY}
state = {"empty": [], "bad_images": [], "bad_hosts": []}


def is_quera(url: str) -> bool:
    host = urlparse(url).hostname or ""
    return host == "quera.org" or host.endswith(".quera.org")


def auth_headers(url: str) -> dict:
    # Send the session cookie only to quera.org, never to third-party hosts
    return {"Cookie": COOKIE} if urlparse(url).hostname == "quera.org" else {}


def safe_name(text: str) -> str:
    # Strip characters that are illegal in file names
    text = re.sub(r'[\\/:*?"<>|\r\n]+', " ", text).strip()
    return re.sub(r"\s+", " ", text)[:80] or "untitled"


def load_state() -> None:
    path = OUT / "state.json"
    if path.exists():
        state.update(json.loads(path.read_text(encoding="utf-8")))
    for key in ("empty", "bad_images", "bad_hosts"):
        state.setdefault(key, [])


def save_state() -> None:
    (OUT / "state.json").write_text(json.dumps(state, indent=2), encoding="utf-8")


def fetch(url: str, attempts: int = 6, retry_network: bool = True) -> requests.Response:
    # GET with retries: honors Retry-After and backs off on 429/5xx
    last_error = None
    for attempt in range(attempts):
        try:
            resp = session.get(url, headers=auth_headers(url), timeout=30)
        except requests.RequestException as exc:
            if not retry_network:
                raise
            last_error = exc
            wait = min(120, 10 * 2 ** attempt)
            print(f"   ~ network error, retry in {wait}s")
            time.sleep(wait)
            continue

        if resp.status_code == 429 or resp.status_code >= 500:
            retry_after = resp.headers.get("Retry-After", "")
            wait = int(retry_after) if retry_after.isdigit() else min(300, 15 * 2 ** attempt)
            delay["current"] = min(MAX_DELAY, delay["current"] * 1.5)
            print(f"   ~ HTTP {resp.status_code}, waiting {wait}s "
                  f"(delay now {delay['current']:.1f}s)")
            last_error = RuntimeError(f"HTTP {resp.status_code}")
            time.sleep(wait)
            continue

        resp.raise_for_status()
        return resp
    raise RuntimeError(f"failed after {attempts} attempts: {url} ({last_error})")


def get_next_data(url: str) -> dict:
    resp = fetch(url)
    match = NEXT_DATA_RE.search(resp.text)
    if not match:
        raise RuntimeError(f"__NEXT_DATA__ not found: {url}")
    data = json.loads(match.group(1))
    user = data["props"]["pageProps"]["globalServerValues"]["currentUser"]
    if not user.get("is_authenticated"):
        raise RuntimeError("Not logged in: check cookies.txt")
    return data["props"]["pageProps"]["college"]["editor"]


def download(url: str, dest: Path) -> bool:
    if dest.exists():  # never hit the server for files we already have
        return True
    host = urlparse(url).hostname or ""
    if url in state["bad_images"] or host in state["bad_hosts"]:
        return False
    try:
        # Images get a short budget: no long network backoff
        dest.write_bytes(fetch(url, attempts=3, retry_network=False).content)
        time.sleep(0.5)
        return True
    except Exception as exc:
        text = repr(exc)
        dns_failure = "NameResolutionError" in text or "Name or service not known" in text
        if dns_failure and not is_quera(url):
            state["bad_hosts"].append(host)  # skip this host from now on
        else:
            state["bad_images"].append(url)
        save_state()
        print(f"   ! image skipped: {url} ({type(exc).__name__})")
        return False


def localize_images(md: str, images_dir: Path) -> str:
    # Download every image and rewrite its URL to a relative local path
    counter = {"n": 0}

    def local_path(url: str) -> str:
        if url.startswith("//"):
            url = "https:" + url
        elif url.startswith("/"):
            url = BASE + url
        if not url.startswith("http"):
            return url
        counter["n"] += 1
        base = safe_name(url.split("?")[0].rsplit("/", 1)[-1]) or "img"
        images_dir.mkdir(parents=True, exist_ok=True)
        name = f"{counter['n']:03d}_{base}"
        return f"images/{name}" if download(url, images_dir / name) else url

    md = MD_IMG_RE.sub(lambda m: f"![{m.group(1)}]({local_path(m.group(2))})", md)
    md = HTML_IMG_RE.sub(
        lambda m: f"{m.group(1)}{local_path(m.group(2))}{m.group(3)}", md
    )
    return md


def localize_projects(md: str, chapter_dir: Path, prefix: str) -> str:
    # Download starter-project archives linked from exercises
    def replace(match):
        dest = chapter_dir / "projects" / f"{prefix}_{match.group(1)}.zip"
        dest.parent.mkdir(parents=True, exist_ok=True)
        if download(BASE + match.group(0), dest):
            return f"projects/{dest.name}"
        return match.group(0)

    return PROJECT_LINK_RE.sub(replace, md)

def download_texts() -> None:
    chapters = get_next_data(START_URL)["assignments"]
    failed = []

    for ci, chapter in enumerate(chapters, 1):
        chapter_dir = OUT / f"{ci:02d}-{safe_name(chapter['name'])}"
        for li, prob in enumerate(chapter["problems"], 1):
            if prob["training_type"] != "LSN" and not INCLUDE_EXERCISES:
                continue
            md_path = chapter_dir / f"{li:02d}-{safe_name(prob['name'])}.md"
            if md_path.exists():
                continue  # already downloaded, no request at all
            if prob["id"] in state["empty"] and not RETRY_EMPTY:
                continue  # known to be empty/locked

            url = f"{BASE}/college/{COLLEGE_ID}/chapter/{chapter['id']}/lesson/{prob['id']}"
            print(f"[{ci}.{li}] {prob['name']}")
            try:
                editor = get_next_data(url)
            except Exception as exc:
                print(f"   ! skipped: {exc}")
                failed.append(f"{ci}.{li} {prob['name']}")
                continue

            desc = (editor["current_problem"].get("description") or "").replace("\r\n", "\n")
            if not desc.strip():
                print("   ! empty (locked or no text)")
                state["empty"].append(prob["id"])
                save_state()
                continue

            chapter_dir.mkdir(parents=True, exist_ok=True)
            desc = localize_images(desc, chapter_dir / "images")
            desc = localize_projects(desc, chapter_dir, f"{li:02d}")
            md_path.write_text(f"# {prob['name']}\n\n{desc}\n", encoding="utf-8")
            time.sleep(delay["current"] + random.uniform(0, 1.5))

    if failed:
        print(f"\n{len(failed)} lesson(s) failed; run the script again:")
        for item in failed:
            print("  -", item)


def decode_video_url(marker_path: str) -> str:
    # The marker is a JWT; its payload holds the m3u8 URL (no network needed)
    payload = marker_path.rsplit("/", 1)[-1].split(".")[1]
    payload += "=" * (-len(payload) % 4)
    return json.loads(base64.urlsafe_b64decode(payload))["video_url"]


def run_ytdlp(url: str, dest: Path) -> bool:
    dest.parent.mkdir(parents=True, exist_ok=True)
    cmd = [
        "yt-dlp", "--no-warnings", "--merge-output-format", "mp4",
        "--retries", "10", "--fragment-retries", "10",
        "--concurrent-fragments", "4",
        "--referer", BASE + "/", "--add-header", f"Origin:{BASE}",
        "-o", str(dest), url,
    ]
    return subprocess.run(cmd).returncode == 0 and dest.exists()


def download_videos() -> None:
    if not shutil.which("yt-dlp"):
        print("yt-dlp not found: pip install yt-dlp (and install ffmpeg)")
        return
    records = []

    for md_path in sorted(OUT.rglob("*.md")):
        text = md_path.read_text(encoding="utf-8")
        if not VIDEO_MARK_RE.search(text):
            continue
        counter = {"n": 0}

        def replace(match):
            counter["n"] += 1
            try:
                url = decode_video_url(match.group(1))
            except Exception as exc:
                print(f"   ! bad video marker in {md_path.name}: {exc}")
                return match.group(0)
            dest = md_path.parent / "videos" / f"{md_path.name[:2]}_{counter['n']}.mp4"
            records.append({"file": str(md_path), "url": url, "video": str(dest)})
            if not dest.exists():
                print(f"[video] {md_path.name} -> {dest.name}")
                if not run_ytdlp(url, dest):
                    print("   ! video download failed, marker kept")
                    return match.group(0)
            return f"![](videos/{dest.name})"

        new_text = VIDEO_MARK_RE.sub(replace, text)
        if new_text != text:
            md_path.write_text(new_text, encoding="utf-8")

    (OUT / "videos.json").write_text(
        json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")


def main():
    args = set(sys.argv[1:])
    OUT.mkdir(parents=True, exist_ok=True)
    load_state()
    if "--videos-only" not in args:
        download_texts()
    if "--texts-only" not in args:
        download_videos()
    print("Done.")


if __name__ == "__main__":
    main()