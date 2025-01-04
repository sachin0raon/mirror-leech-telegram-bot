from apscheduler.schedulers.asyncio import AsyncIOScheduler
from aria2p import API as ariaAPI, Client as ariaClient, ClientException
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
from qbittorrentapi import Client as QbClient, exceptions as QbExceptions
from sabnzbdapi import SabnzbdClient
from socket import setdefaulttimeout
from time import time
from tzlocal import get_localzone
from uvloop import install
from urllib3.exceptions import HTTPError
from requests import get as RequestsGet, exceptions as RequestsExceptions
from .helper.ext_utils.bot_utils import is_empty_or_blank
from .core.config_manager import Config

Config.load()

# from faulthandler import enable as faulthandler_enable
# faulthandler_enable()

install()
setdefaulttimeout(600)

getLogger("qbittorrentapi").setLevel(WARNING)
getLogger("requests").setLevel(WARNING)
getLogger("urllib3").setLevel(WARNING)
getLogger("pyrogram").setLevel(ERROR)
getLogger("httpx").setLevel(WARNING)
getLogger("pymongo").setLevel(WARNING)
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
non_queued_dl = set()
non_queued_up = set()
multi_tags = set()
task_dict_lock = Lock()
queue_dict_lock = Lock()
qb_listener_lock = Lock()
nzb_listener_lock = Lock()
jd_lock = Lock()
cpu_eater_lock = Lock()
same_directory_lock = Lock()
extension_filter = ["aria2", "!qB"]
drives_names = []
drives_ids = []
index_urls = []

try:
    aria2 = ariaAPI(ariaClient(
        host="http://localhost" if is_empty_or_blank(Config.ARIA_HOST) else Config.ARIA_HOST,
        port=6800 if Config.ARIA_PORT is None else Config.ARIA_PORT,
        secret="testing123" if is_empty_or_blank(Config.ARIA_SECRET) else Config.ARIA_SECRET))
except (HTTPError, ClientException, RequestsExceptions.RequestException) as e:
    LOGGER.error(f"Failed to initialize aria2c :: {str(e)}")

try:
    qbittorrent_client = QbClient(
        host="localhost",
        port=8090,
        VERIFY_WEBUI_CERTIFICATE=False,
        REQUESTS_ARGS={"timeout": (30, 60)},
        HTTPADAPTER_ARGS={
            "pool_maxsize": 500,
            "max_retries": 10,
            "pool_block": True,
        },
    )
except QbExceptions.APIError as e:
    LOGGER.error(f"Failed to initialize qbittorrent :: {str(e)}")

sabnzbd_client = SabnzbdClient(
    host="http://localhost",
    api_key="mltb",
    port="8070",
)

scheduler = AsyncIOScheduler(timezone=str(get_localzone()), event_loop=bot_loop)

if not is_empty_or_blank(Config.TOKEN_PICKLE_FILE_URL):
    LOGGER.info("Downloading token.pickle file")
    try:
        pickle_file = RequestsGet(url=Config.TOKEN_PICKLE_FILE_URL, timeout=5)
    except RequestsExceptions.RequestException:
        LOGGER.error("Failed to download token.pickle file")
    else:
        if pickle_file.ok:
            with open("/usr/src/app/token.pickle", 'wb') as f:
                f.write(pickle_file.content)
        else:
            LOGGER.warning("Failed to get pickle file data")
        pickle_file.close()

if not is_empty_or_blank(Config.COOKIE_FILE_URL):
    LOGGER.info("Downloading cookie file")
    try:
        cookie_file = RequestsGet(url=Config.COOKIE_FILE_URL, timeout=5)
    except RequestsExceptions.RequestException:
        LOGGER.error("Failed to download cookie file")
    else:
        if cookie_file.ok:
            with open("/usr/src/app/cookies.txt", 'wt', encoding='utf-8') as f:
                f.write(cookie_file.text)
        else:
            LOGGER.warning("Failed to get cookie file data")
        cookie_file.close()
