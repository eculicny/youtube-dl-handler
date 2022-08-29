import os
from loguru import logger
import time
import requests
from pathlib import Path
from datetime import datetime
from yt_dlp import YoutubeDL

from src.config_parser import parse_config, DownloadType, DownloadConfig

GOTIFY_APP_TOKEN = os.environ["GOTIFY_APP_TOKEN"]
GOTIFY_URL = os.environ["GOTIFY_URL"]
NO_GOTIFY_PUSH = True

DOWNLOADER_DIR = Path(os.environ["VIDEO_DOWNLOADER_DIR"])
LANDING_DIR = Path(os.environ["VIDEO_LANDING_DIR"])
SLEEP_INTERVAL = os.environ.get("SLEEP_INTERVAL", 300)
PICKUP_DIR = DOWNLOADER_DIR / "pickup"
PROCESSED_DIR = DOWNLOADER_DIR / "processed"
ERRORS_DIR = DOWNLOADER_DIR / "errors"
RETRIES = 2


def push_gotify_message(message: str, priority: int) -> None:
    if not NO_GOTIFY_PUSH:
        try:
            resp = requests.post(f'{GOTIFY_URL}/message?token={GOTIFY_APP_TOKEN}',
                                json={
                                    "message": message,
                                    "priority": priority,
                                    "title": "Youtube-dl Daemon"
                                }
            )
            resp.raise_for_status()
            logger.info("Sent message to gotify server.")
        except requests.RequestException as exc:
            logger.exception(f"Failed to send message to gotify server: {message}")
    else:
        logger.info(f"Skipping gotify message: {message}")


def move_file(file_path: Path, target_dir: Path, execution_time: str) -> None:
    new_path = target_dir / (file_path.name + "_" + execution_time)
    logger.debug(f"Moving {str(file_path.absolute())} to {str(new_path.absolute())}.")
    file_path.rename(new_path)
    return new_path


def process_dconfig(dconf: DownloadConfig, dl_opts: dict) -> bool:
    error_count = 0
    while error_count < RETRIES:
        try:
            if dconf.opts:
                all_opts = zip(dl_opts, dconf.opts)
            else:
                all_opts = dl_opts.copy()
            logger.info(f"Downloading video at urls: {dconf.urls} Attempt {error_count}")
            with YoutubeDL(all_opts) as ydl:
                ydl.download(dconf.urls)
            logger.info(f"Successfully downloaded url list: {dconf.urls}")
            return True
        except Exception as err:
            error_count += 1
            logger.warning(f"Failed to process url list {error_count} times. Exception: {err}")

    logger.exception(f"Failed to process url list {error_count} times.")
    return False


def process_file(file_path: Path, error_count: int) -> bool:
    execution_time = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    logger.info(f"Processing {file_path.absolute()} with execution timestamp {execution_time}.")
    config = parse_config(file_path)
    ydl_opts = {
        "verbose": True,
        "outtmpl": f"{str(LANDING_DIR.absolute())}/{file_path.name}/%(title)s.%(ext)s",
        "ignoreerrors": False
    }

    if config.type == DownloadType.AUDIO:
        ydl_opts["format"] = "ba*/b"
        #'format': 'm4a/bestaudio/best',
        # ℹ️ See help(yt_dlp.postprocessor) for a list of available Postprocessors and their arguments
        # Extract audio using ffmpeg
        ydl_opts["postprocessors"]= [{
            "key": 'FFmpegExtractAudio',
            #"preferredcodec": "m4a",
            "preferredquality": 0,
        }]
        # TODO add remove downloaded file and postprocessing command
    if config.type == DownloadType.VIDEO:
        ydl_opts["format"] = "bv*+ba/b"
        if config.subtitles:
            ydl_opts["subtitlesformat"] = "vtt/srt/best"
            ydl_opts["writesubtitles"] = True
            ydl_opts["writeautomaticsub"] = True
    if config.rate_limit:
        ydl_opts["ratelimit"] = config.rate_limit
    if config.cookies:
        ydl_opts["cookiefile"] = str((DOWNLOADER_DIR / "cookies.txt").absolute())


    success = True
    for dconf in config.items:
        success = (process_dconfig(dconf, ydl_opts) and success)

    if success:
        move_file(file_path, PROCESSED_DIR, execution_time)
        return True
    else:
        # error_count += 1
        # logger.error(f"Failed to process {file_path.name} {error_count} times. Exception: {file_exc}")
        # if error_count > RETRIES:
        #     moved_file = move_file(file_path, ERRORS_DIR, execution_time)
        #     (moved_file.parent / (moved_file.name + "_log")).write_text(str(file_exc))
        move_file(file_path, ERRORS_DIR, execution_time)
        return False


if __name__ == "__main__":
    try:
        [ path.mkdir(exist_ok=True) for path in [PICKUP_DIR, ERRORS_DIR, PROCESSED_DIR, LANDING_DIR] ]
        dont_stop = True
        error_counts = {}
        while(dont_stop):
            try:
                count = 0
                errors = 0
                for unproc in PICKUP_DIR.glob('*'):
                    if unproc.is_file:
                        success = process_file(unproc, error_counts.get(unproc.name, 0))
                        if success:
                            logger.info(f"Successfully processed {unproc.name}.")
                            if unproc.name in error_counts:
                                del error_counts[unproc.name]
                            count += 1
                        else:
                            logger.error(f"Failed to process {unproc.name}.")
                            error_counts[unproc.name] = error_counts.get(unproc.name, 0) + 1
                            errors += 1
                            push_gotify_message(
                                f"Encountered error processing file {unproc.name}",
                                5
                            )
                    else:
                        logger.warning(f"Skipping non-file {unproc.name}")
                logger.info(f"Processed: {count}. Failed: {errors}.")
                logger.info(f"Sleeping for {SLEEP_INTERVAL}s")
                time.sleep(SLEEP_INTERVAL)
            except KeyboardInterrupt:
                logger.warning("Keyboard interrupt encountered. Stopping loop.")
                dont_stop = False
        logger.info("Shutting down")
    except Exception as exc:
        logger.exception(f"Unexpected fatal exception encountered.")
        push_gotify_message(
            f"Service shutting down. Fatal error encountered {str(exc)}",
            7
        )
