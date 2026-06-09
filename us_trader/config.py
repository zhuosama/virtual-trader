"""
配置加载:读取内置默认值,可用外部 JSON 文件覆盖(深合并)。
"""
import json
import copy
from pathlib import Path

_DEFAULT_CONFIG_PATH = Path(__file__).parent / "config.json"


def _deep_merge(base: dict, override: dict) -> dict:
    """递归合并 override 到 base 副本中,返回新 dict。"""
    result = copy.deepcopy(base)
    for k, v in override.items():
        if k in result and isinstance(result[k], dict) and isinstance(v, dict):
            result[k] = _deep_merge(result[k], v)
        else:
            result[k] = copy.deepcopy(v)
    return result


def load_config(path: str = None) -> dict:
    """
    加载配置。
    - path=None:返回内置默认 config.json。
    - path=<文件路径>:读取该文件并与默认值深合并(文件中的键覆盖默认值)。
    """
    with open(_DEFAULT_CONFIG_PATH, "r", encoding="utf-8") as f:
        defaults = json.load(f)

    if path is None:
        return defaults

    with open(path, "r", encoding="utf-8") as f:
        override = json.load(f)

    return _deep_merge(defaults, override)
