from . import LOGGER, bot_loop, is_empty_or_blank, DOWNLOAD_DIR
from .core.mltb_client import TgClient
from pyngrok import ngrok, conf
from requests import get as RequestsGet, exceptions as RequestsExceptions

async def main():
    from asyncio import gather
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

    Config.load()
    download_token_file(Config.TOKEN_PICKLE_FILE_URL)
    download_cookie_file(Config.COOKIE_FILE_URL)
    if not is_empty_or_blank(Config.NGROK_AUTH_TOKEN):
        await start_ngrok(Config.NGROK_AUTH_TOKEN)
    await load_settings()

    await gather(TgClient.start_bot(), TgClient.start_user())
    await gather(load_configurations(), update_variables())

    from .core.torrent_manager import TorrentManager

    await TorrentManager.initiate()
    await gather(
        update_qb_options(),
        update_aria2_options(),
        update_nzb_options(),
    )
    from .helper.ext_utils.files_utils import clean_all
    from .core.jdownloader_booter import jdownloader
    from .helper.ext_utils.telegraph_helper import telegraph
    from .helper.mirror_leech_utils.rclone_utils.serve import rclone_serve_booter
    from .modules import (
        initiate_search_tools,
        get_packages_version,
        restart_notification,
    )

    await gather(
        save_settings(),
        jdownloader.boot(),
        clean_all(),
        initiate_search_tools(),
        get_packages_version(),
        restart_notification(),
        telegraph.create_account(),
        rclone_serve_booter(),
    )


bot_loop.run_until_complete(main())

from .helper.ext_utils.bot_utils import create_help_buttons
from .helper.listeners.aria2_listener import add_aria2_callbacks
from .core.handlers import add_handlers

add_aria2_callbacks()
create_help_buttons()
add_handlers()

from pyrogram.filters import regex
from pyrogram.handlers import CallbackQueryHandler

from .core.handlers import add_handlers
from .helper.ext_utils.bot_utils import new_task
from .helper.telegram_helper.filters import CustomFilters
from .helper.telegram_helper.message_utils import (
    send_message,
    edit_message,
    delete_message,
)

def download_token_file(token_file_url: str):
    if not is_empty_or_blank(token_file_url):
        LOGGER.info("Downloading token.pickle file")
        try:
            pickle_file = RequestsGet(url=token_file_url, timeout=5)
        except RequestsExceptions.RequestException:
            LOGGER.error("Failed to download token.pickle file")
        else:
            if pickle_file.ok:
                with open("/usr/src/app/token.pickle", 'wb') as f:
                    f.write(pickle_file.content)
            else:
                LOGGER.warning("Failed to get pickle file data")
            pickle_file.close()

def download_cookie_file(cookie_file_url):
    if not is_empty_or_blank(cookie_file_url):
        LOGGER.info("Downloading cookie file")
        try:
            cookie_file = RequestsGet(url=cookie_file_url, timeout=5)
        except RequestsExceptions.RequestException:
            LOGGER.error("Failed to download cookie file")
        else:
            if cookie_file.ok:
                with open("/usr/src/app/cookies.txt", 'wt', encoding='utf-8') as f:
                    f.write(cookie_file.text)
            else:
                LOGGER.warning("Failed to get cookie file data")
            cookie_file.close()

@new_task
async def start_ngrok(auth_token: str) -> None:
    LOGGER.info("Starting ngrok tunnel")
    with open("/usr/src/app/ngrok.yml", "w") as config:
        config.write(f"version: 2\nauthtoken: {auth_token}\nregion: in\nconsole_ui: false\nlog_level: info")
    ngrok_conf = conf.PyngrokConfig(
        config_path="/usr/src/app/ngrok.yml",
        auth_token=auth_token,
        region="in",
        max_logs=5,
        ngrok_version="v3",
        monitor_thread=False)
    try:
        conf.set_default(ngrok_conf)
        file_tunnel = ngrok.connect(addr=f"file://{DOWNLOAD_DIR}", proto="http", schemes=["https"], name="files_tunnel", inspect=False)
        LOGGER.info(f"Ngrok tunnel started: {file_tunnel.public_url}")
    except ngrok.PyngrokError as err:
        LOGGER.error(f"Failed to start ngrok, error: {str(err)}")

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


TgClient.bot.add_handler(
    CallbackQueryHandler(
        restart_sessions_confirm,
        filters=regex("^sessionrestart") & CustomFilters.sudo,
    )
)

LOGGER.info("Bot Started!")
bot_loop.run_forever()
