# quera-dl

A small Python script that backs up a [Quera College](https://quera.org/college)
course you have **purchased** for offline, personal use: lesson and exercise
texts (as Markdown), images, starter projects, and videos.

> **Disclaimer:** This tool is for personal backups of content you have legally
> access to. Course material is copyrighted. Do not redistribute it, and check
> Quera's terms of service before using this. The authors take no responsibility
> for misuse.

## Features

- Saves each lesson/exercise as a Markdown file, organized by chapter
- Downloads images and rewrites links to local relative paths
- Downloads starter-project archives linked from exercises
- Downloads videos via `yt-dlp` and links them in the Markdown
- Resumable: existing files are skipped, so re-running only fetches what is missing
- Polite: delay with jitter between requests, backs off on HTTP 429/5xx
- Sends your session cookie only to `quera.org`, never to third-party hosts

## Requirements

- Python 3.10+
- [`ffmpeg`](https://ffmpeg.org/) (for merging video streams): `sudo apt install ffmpeg`
- Python packages: `pip install -r requirements.txt`

## Setup

```bash
git clone git@github.com:PiByte/Quera_dl.git
cd Quera-dl
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

### Authentication

1. Log in to Quera in your browser and open any lesson page of your course.
2. Open DevTools -> Network, reload the page, and click the main document request.
3. Copy the value of the `Cookie` request header.
4. Paste it as a **single line** into a file named `cookies.txt` next to the script.

`cookies.txt` is git-ignored. Never commit it or share it: anyone with it can
act as you on Quera.

### Configuration

Edit the constants at the top of `quera_dl.py`:

| Constant | Meaning |
|---|---|
| `COLLEGE_ID` | Course ID, from the course URL (`/college/<id>/...`) |
| `START_URL` | Any lesson URL of the course (used to read the chapter list) |
| `OUT` | Output directory (default: `output/`) |
| `INCLUDE_EXERCISES` | Also save exercise (PRO) problems |
| `RETRY_EMPTY` | Re-request lessons previously recorded as empty (set back to `False` afterwards) |
| `BASE_DELAY` / `MAX_DELAY` | Delay between lesson requests, in seconds |

## Usage

```bash
python quera_dl.py                 # texts, then videos
python quera_dl.py --texts-only    # skip videos
python quera_dl.py --videos-only   # only process videos found in saved texts
```

Re-run the script at any time; it picks up where it stopped.

## Output layout

```
output/
├── 01-<chapter>/
│   ├── 01-<lesson>.md
│   ├── images/
│   ├── projects/
│   └── videos/
├── state.json      # known-empty lessons and failed image hosts
└── videos.json     # video URL records
```

## Troubleshooting

- **`Not logged in: check cookies.txt`**: the cookie expired; copy a fresh one.
- **Exercise saved as `empty (locked or no text)`**: the exercise text may not be
  included in the page data for your account. Open an issue with the request
  you see in DevTools (never include your cookie).
- **Corrupted `.zip` in `projects/`**: check with `file output/*/projects/*.zip`.
  Delete the bad file and run again.
