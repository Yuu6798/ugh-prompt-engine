"""uv の期限つき waiver（[tool.uv.exclude-newer-package]）が放置されていないことを検証する。

グローバルの `exclude-newer = "7 days"` は相対指定で常に 7 日窓として効くが、
`exclude-newer-package` の値は絶対日時のため、7 日窓を過ぎても残り続け、そのパッケージ
だけを黙って固定してしまう。waiver は「公開 7 日未満の修正版を既知 advisory のために
先取りする」一時措置なので、期限切れは CI の失敗で大きく知らせる。
"""

from __future__ import annotations

import tomllib
from datetime import datetime, timedelta, timezone

from ._helpers import REPO_ROOT


def test_uv_exclude_newer_package_waivers_are_not_expired() -> None:
    config = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    waivers = config.get("tool", {}).get("uv", {}).get("exclude-newer-package", {})
    now = datetime.now(timezone.utc)
    expired = []
    for package, value in waivers.items():
        stamp = value if isinstance(value, datetime) else datetime.fromisoformat(value)
        stamp = stamp.replace(tzinfo=stamp.tzinfo or timezone.utc)
        if now - stamp > timedelta(days=14):
            expired.append(f"{package} ({stamp.isoformat()})")
    assert not expired, (
        f"uv の exclude-newer-package waiver が 14 日を超過: {', '.join(expired)}。"
        "pyproject.toml から該当 waiver を削除して `uv lock` を実行すること。"
    )
