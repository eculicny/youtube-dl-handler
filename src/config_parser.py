from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import List, Tuple
import yaml


class DownloadType(Enum):
    VIDEO = 1
    AUDIO = 2


@dataclass
class DownloadConfig:
    urls: Tuple[str]
    opts: dict


@dataclass
class ManifestConfig:
    type: DownloadType
    items: List[DownloadConfig]
    subtitles: bool = False
    rate_limit: str = None
    cookies: bool = False


def parse_config(config_path: Path) -> ManifestConfig:
    config_content = config_path.read_text()
    config_file = yaml.safe_load(config_content)
    config = config_file["config"]
    config["type"] = DownloadType.AUDIO if config["type"].lower() == "audio" else DownloadType.VIDEO
    download_configs = []
    for val in config_file["items"]:
        download_configs.append(
            DownloadConfig(
                val["urls"],
                val.get("opts", None)
            )
        )
    return ManifestConfig(**config_file["config"], items=download_configs)
