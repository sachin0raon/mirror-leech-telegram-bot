from signal import signal, SIGINT
from asyncio import gather
from pyrogram.filters import regex
from pyrogram.handlers import CallbackQueryHandler
from apscheduler.triggers.interval import IntervalTrigger
from datetime import datetime, timedelta
from . import LOGGER, bot_loop, scheduler, task_dict_lock
from .core.config_manager import Config
from .core.startup import (
    load_settings,
    load_configurations,
    save_settings,
    update_aria2_options,
    update_nzb_options,
    update_qb_options,
    update_variables,
)

bot_loop.run_until_complete(load_settings())

from .core.mltb_client import TgClient
from .core.handlers import add_handlers
from .helper.ext_utils.telegraph_helper import telegraph
from .helper.ext_utils.bot_utils import sync_to_async, create_help_buttons, new_task, is_empty_or_blank
from .helper.ext_utils.files_utils import clean_all, exit_clean_up
from .helper.ext_utils.jdownloader_booter import jdownloader
from .helper.listeners.aria2_listener import start_aria2_listener
from .helper.telegram_helper.filters import CustomFilters
from .helper.mirror_leech_utils.rclone_utils.serve import rclone_serve_booter
from .helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    delete_message,
)
from .modules import initiate_search_tools, get_packages_version, restart_notification
from pyngrok import ngrok, conf
import requests


@new_task
async def restart_sessions_confirm(_, query):
    data = query.data.split()
    message = query.message
    if data[1] == "confirm":
        reply_to = message.reply_to_message
        restart_message = await send_message(reply_to, "Restarting Session(s)...")
        await delete_message(message)
        await TgClient.reload()
        add_handlers()
        TgClient.bot.add_handler(
            CallbackQueryHandler(
                restart_sessions_confirm,
                filters=regex("^sessionrestart") & CustomFilters.sudo,
            )
        )
        await edit_message(restart_message, "Session(s) Restarted Successfully!")
    else:
        await delete_message(message)


@new_task
async def start_ngrok() -> None:
    LOGGER.info("Starting ngrok tunnel")
    with open("/usr/src/app/ngrok.yml", "w") as config:
        config.write(f"version: 2\nauthtoken: {Config.NGROK_AUTH_TOKEN}\nregion: in\nconsole_ui: false\nlog_level: info")
    ngrok_conf = conf.PyngrokConfig(
        config_path="/usr/src/app/ngrok.yml",
        auth_token=Config.NGROK_AUTH_TOKEN,
        region="in",
        max_logs=5,
        ngrok_version="v3",
        monitor_thread=False)
    try:
        conf.set_default(ngrok_conf)
        file_tunnel = ngrok.connect(addr=f"file://{Config.DOWNLOAD_DIR}", proto="http", schemes=["https"], name="files_tunnel", inspect=False)
        LOGGER.info(f"Ngrok tunnel started: {file_tunnel.public_url}")
    except ngrok.PyngrokError as err:
        LOGGER.error(f"Failed to start ngrok, error: {str(err)}")


async def get_trackers() -> None:
    LOGGER.info("Fetching trackers list")
    async with task_dict_lock:
        Config.BT_TRACKERS.clear()
        Config.BT_TRACKERS_ARIA = '['
        for index, url in enumerate(Config.BT_TRACKER_URLS):
            try:
                track_resp = requests.get(url=url, timeout=5)
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
            except requests.exceptions.RequestException:
                LOGGER.error(f"Failed to send request to :: {url}")
        Config.BT_TRACKERS_ARIA += ']'
    LOGGER.info(f"Retrieved {len(Config.BT_TRACKERS)} trackers")


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


async def main():
    if not is_empty_or_blank(Config.NGROK_AUTH_TOKEN):
        await start_ngrok()
    await load_settings()
    await gather(TgClient.start_bot(), TgClient.start_user())
    await gather(load_configurations(), update_variables())
    await gather(
        sync_to_async(update_qb_options),
        sync_to_async(update_aria2_options),
        update_nzb_options(),
    )
    await gather(
        save_settings(),
        jdownloader.boot(),
        #sync_to_async(clean_all),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
        rclone_serve_booter(),
        sync_to_async(start_aria2_listener, wait=False),
    )
    create_help_buttons()
    add_handlers()
    TgClient.bot.add_handler(
        CallbackQueryHandler(
            restart_sessions_confirm,
            filters=regex("^sessionrestart") & CustomFilters.sudo,
        )
    )
    LOGGER.info("Bot Started!")
    signal(SIGINT, exit_clean_up)


bot_loop.run_until_complete(main())
bot_loop.run_forever()
