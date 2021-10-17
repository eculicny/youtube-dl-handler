import os
from loguru import logger
import time
import requests
import shlex
from pathlib import Path
from datetime import datetime
import youtube_dl
import configparser
import subprocess

GOTIFY_APP_TOKEN = "ALUL8ScjPWGS6ea" #os.environ["GOTIFY_APP_TOKEN"]  # TODO switch
GOTIFY_URL = "https://gotify.ulicny.io" #os.environ["GOTIFY_URL"]  # TODO switch

DOWNLOADER_DIR = Path(os.environ["VIDEO_DOWNLOADER_DIR"])
LANDING_DIR = Path(os.environ["VIDEO_LANDING_DIR"])
SLEEP_INTERVAL = os.environ.get("SLEEP_INTERVAL", 300)
PICKUP_DIR = DOWNLOADER_DIR / "pickup"
PROCESSED_DIR = DOWNLOADER_DIR / "processed"
ERRORS_DIR = DOWNLOADER_DIR / "errors"
MUXED_DIR = LANDING_DIR / "muxed"
MUXED_TEST_DIR = LANDING_DIR / "test"
RETRIES = 2


def push_gotify_message(message: str, priority: int) -> None:
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


def move_file(file_path: Path, target_dir: Path, execution_time: str) -> None:
    new_path = target_dir / (file_path.name + "_" + execution_time)
    logger.debug(f"Moving {str(file_path.absolute())} to {str(new_path.absolute())}.")
    file_path.rename(new_path)
    return new_path


# TODO add switch for audio vs playlist vs video
# TODO switch list file to be [video]/[audio]/[playlist]
def process_file(file_path: Path, error_count: int) -> bool:
    execution_time = datetime.utcnow().strftime("%Y%m%d_%H%M%S")
    file_exc = None
    logger.info(f"Processing {file_path.absolute()} with execution timestamp {execution_time}.")
    try:
        ydl_opts = {
            #"simulate": True,
            #"noprogress": True,
            "verbose": True,
            "outtmpl": f"{str(LANDING_DIR.absolute())}/%(id)s/%(title)s-%(id)s.%(ext)s",
            "format": "(bestvideo[ext=mp4],bestaudio[ext=m4a])/(bestvideo,bestaudio)/best",
            "subtitlesformat": "vtt/srt/best",
            "writesubtitles": True,
            "writeautomaticsub": True,
            "ratelimit": 2*(1024.0**2),
            "ignoreerrors": False,
            # TODO add playlist switching
            # TODO add sleep interval for playlists
            "noplaylist": True,
            "sleep_interval": 10,
            "max_sleep_interval": 60,
        }
        if "cookies" in file_path.name:
            logger.info(f"Setting cookies for file {file_path.name}")
            ydl_opts["cookiefile"] = str((DOWNLOADER_DIR / "cookies.txt").absolute())

        video_urls = []
        content = file_path.read_text()
        lines = content.split("\n")
        video_urls = [ l.strip() for l in lines if l.strip() != "" ]
        logger.info(f"Downloading video at urls: {video_urls}")
        with youtube_dl.YoutubeDL(ydl_opts) as ydl:
            ydl.download(video_urls)
        move_file(file_path, PROCESSED_DIR, execution_time)
        return True, file_exc
    except Exception as exc:
        file_exc = exc
        error_count += 1
        logger.error(f"Failed to process {file_path.name} {error_count} times. Exception: {file_exc}")
        if error_count > RETRIES:
            moved_file = move_file(file_path, ERRORS_DIR, execution_time)
            (moved_file.parent / (moved_file.name + "_log")).write_text(str(file_exc))
    return False, file_exc


if __name__ == "__main__":
    try:
        [ path.mkdir(exist_ok=True) for path in [PICKUP_DIR, ERRORS_DIR, PROCESSED_DIR, LANDING_DIR, MUXED_DIR] ]
        dont_stop = True
        error_counts = {}
        while(dont_stop):
            try:
                count = 0
                errors = 0
                for unproc in PICKUP_DIR.glob('*'):
                    if unproc.is_file:
                        success, file_exc = process_file(unproc, error_counts.get(unproc.name, 0))
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
                                f"Encountered error processing file {unproc.name}: {file_exc}",
                                5
                            )
                    else:
                        logger.warning(f"Skipping non-file {unproc.name}")
                logger.info(f"Processed: {count}. Failed: {errors}. Sleeping for {SLEEP_INTERVAL}s")

                ffmpeg_language_meta = "language=eng"
                for unmux in LANDING_DIR.glob('*'):
                    if unmux not in [MUXED_DIR, MUXED_TEST_DIR]:
                        logger.info(f"Muxing directory {unmux}")
                        original_files = []
                        proc = None
                        try:
                            # ffmpeg [-i <file> for file] -c:v libx265 -c:a copy -c:s srt -metadata:s:s:0 language=eng -metadata:s:a:0 language=eng <output>.mkv
                            # move all files to muxed
                            command = ["ffmpeg", "-y"]  # ffmpeg (force overwrite)
                            output_file = None
                            for input_file in unmux.glob('*'):
                                original_files.append(input_file)
                                command.append("-i")
                                command.append(str(input_file.absolute()))
                                if not output_file:
                                    output_file = f"{str(input_file.parent.absolute())}/{input_file.name[:input_file.name.find('.')]}.mkv"
                            if not output_file:
                                output_file = f"{str(unmux.absolute())}/{unmux.name}.mkv"
                            # video codec transcode
                            command.append("-c:v")
                            command.append("libx264")
                            # audio codec transcode (just copy)
                            command.append("-c:a")
                            command.append("copy")
                            command.append("-metadata:s:a:0")
                            command.append(ffmpeg_language_meta)
                            # subtitles codec transcode
                            command.append("-c:s")
                            command.append("srt")
                            command.append("-metadata:s:s:0")
                            command.append(ffmpeg_language_meta)
                            # final output file
                            command.append(output_file)
                            logger.info(f"Running ffmpeg command: {command}")
                            proc = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, universal_newlines=True)
                            for line in iter(proc.stdout.readline, ""):
                                logger.info(line.strip())
                            ret_code = proc.wait()
                            if ret_code != 0:
                                breakpoint()
                                logger.error(f"Failed to run ffmpeg on directory{unmux.name}")
                                for dir_file in unmux.glob('*'):
                                    if dir_file not in original_files:
                                        logger.info(f"Removing unoriginal file {str(dir_file.absolute())}")
                                        dir_file.unlink()
                            else:
                                logger.info(f"Successfully muxed {unmux.name}")
                                # TODO clean up source files?
                        except Exception as exc:
                            logger.error(f"Failed to mux directory {str(unmux.absolute())}. {exc}")
                            for dir_file in unmux.glob('*'):
                                if dir_file not in original_files:
                                    logger.info(f"Removing unoriginal file {str(dir_file.absolute())}")
                                    dir_file.unlink()
                        except KeyboardInterrupt as exc:
                            logger.warning(f"Keyboard interrupt while muxing directory {str(unmux.absolute())}. {exc}")
                            if proc:
                                proc.kill()
                            for dir_file in unmux.glob('*'):
                                if dir_file not in original_files:
                                    logger.info(f"Removing unoriginal file {str(dir_file.absolute())}")
                                    dir_file.unlink()
                            raise
                logger.info(f"Sleeping for {SLEEP_INTERVAL}s")
                time.sleep(SLEEP_INTERVAL)
            except KeyboardInterrupt:
                # TODO verify way to close out ffmpeg calls?
                logger.warning("Keyboard interrupt encountered. Stopping loop.")
                dont_stop = False
        logger.info("Shutting down")
    except Exception as exc:
        logger.critical(f"Unexpected fatal exception encountered. {str(exc)}")
        push_gotify_message(
            f"Service shutting down. Fatal error encountered {str(exc)}",
            7
        )
