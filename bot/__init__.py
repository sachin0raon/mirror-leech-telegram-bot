from uvloop import install

install()
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from asyncio import Lock, new_event_loop, set_event_loop
from logging import (
    getLogger,
    FileHandler,
    StreamHandler,
    INFO,
    basicConfig,
    WARNING,
    ERROR,
)
from sabnzbdapi import SabnzbdClient
from time import time
from tzlocal import get_localzone
from datetime import datetime, timedelta
from core.config_manager import Config
from os import cpu_count, makedirs, path as ospath
from requests import get as RequestsGet, exceptions as RequestsExceptions

getLogger("requests").setLevel(WARNING)
getLogger("urllib3").setLevel(WARNING)
getLogger("pyrogram").setLevel(ERROR)
getLogger("httpx").setLevel(WARNING)
getLogger("pymongo").setLevel(WARNING)
getLogger("aiohttp").setLevel(WARNING)
getLogger("pyngrok.ngrok").setLevel(ERROR)
getLogger("pyngrok.process").setLevel(ERROR)

bot_start_time = time()

bot_loop = new_event_loop()
set_event_loop(bot_loop)

basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    handlers=[FileHandler("log.txt"), StreamHandler()],
    level=INFO,
)

LOGGER = getLogger(__name__)
cpu_no = cpu_count()

DOWNLOAD_DIR = "/usr/src/app/downloads/"
intervals = {"status": {}, "qb": "", "jd": "", "nzb": "", "stopAll": False}
qb_torrents = {}
jd_downloads = {}
nzb_jobs = {}
user_data = {}
aria2_options = {}
qbit_options = {}
nzb_options = {}
queued_dl = {}
queued_up = {}
status_dict = {}
task_dict = {}
rss_dict = {}
auth_chats = {}
excluded_extensions = ["aria2", "!qB"]
drives_names = []
drives_ids = []
index_urls = []
sudo_users = []
non_queued_dl = set()
non_queued_up = set()
multi_tags = set()
task_dict_lock = Lock()
queue_dict_lock = Lock()
qb_listener_lock = Lock()
nzb_listener_lock = Lock()
jd_listener_lock = Lock()
cpu_eater_lock = Lock()
same_directory_lock = Lock()

if ospath.exists(DOWNLOAD_DIR) is False:
    makedirs(name=DOWNLOAD_DIR, exist_ok=True)
    makedirs(name=f"{DOWNLOAD_DIR}ytdl/audio", exist_ok=True)
    makedirs(name=f"{DOWNLOAD_DIR}ytdl/video", exist_ok=True)

sabnzbd_client = SabnzbdClient(
    host="http://localhost",
    api_key="mltb",
    port="8070",
)

def is_empty_or_blank(value: str):
    return value is None or not value.strip()

async def get_trackers() -> None:
    LOGGER.info("Fetching trackers list")
    async with task_dict_lock:
        Config.BT_TRACKERS.clear()
        Config.BT_TRACKERS_ARIA = '['
        for index, url in enumerate(Config.BT_TRACKER_URLS):
            try:
                track_resp = RequestsGet(url=url, timeout=5)
                if track_resp.ok:
                    if 0 <= index <= 1:
                        sep = '\n\n'
                    else:
                        sep = '\n'
                    for tracker in track_resp.text.split(sep=sep):
                        Config.BT_TRACKERS.append(tracker.strip())
                        Config.BT_TRACKERS_ARIA += f"{tracker.strip()},"
                    track_resp.close()
                else:
                    LOGGER.error(f"Failed to get data from :: {url}")
            except RequestsExceptions.RequestException:
                LOGGER.error(f"Failed to send request to :: {url}")
        Config.BT_TRACKERS_ARIA += ']'
    LOGGER.info(f"Retrieved {len(Config.BT_TRACKERS)} trackers")

scheduler = AsyncIOScheduler(timezone=str(get_localzone()), event_loop=bot_loop)
scheduler.add_job(
        get_trackers,
        trigger=IntervalTrigger(hours=12),
        id="BT_TRACKERS",
        name="GET_TRACKERS",
        misfire_grace_time=15,
        max_instances=1,
        next_run_time=datetime.now() + timedelta(seconds=5),
        replace_existing=True,
    )
