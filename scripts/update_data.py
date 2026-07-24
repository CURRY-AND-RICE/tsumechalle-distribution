#!/usr/bin/env python3
"""詰めチャレランキングを全件取得し、匿名の公開用JSONを生成する。"""

from __future__ import annotations

import argparse
import json
import logging
import math
import random
import sys
import tempfile
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from sample import (  # noqa: E402
    DEFAULT_ENV,
    DEFAULT_HOST,
    DEFAULT_PORT,
    DEFAULT_RANKING_API_KEY,
    QuestRankingClient,
    parse_user_entry,
    random_vendor_id,
)

LOGGER = logging.getLogger("update-ranking")
JST = timezone(timedelta(hours=9), name="JST")
SCHEDULE_WEEKDAY = 0  # Monday
SCHEDULE_HOUR = 3
SCHEDULE_MINUTE = 15


def iso_utc(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def next_scheduled_update(now: datetime) -> datetime:
    local = now.astimezone(JST)
    days = (SCHEDULE_WEEKDAY - local.weekday()) % 7
    candidate = (local + timedelta(days=days)).replace(
        hour=SCHEDULE_HOUR,
        minute=SCHEDULE_MINUTE,
        second=0,
        microsecond=0,
    )
    if candidate <= local:
        candidate += timedelta(days=7)
    return candidate.astimezone(timezone.utc)


def parse_rating(value: str) -> int:
    try:
        rating = int(value)
    except ValueError as exc:
        raise ValueError(f"レーティングが整数ではありません: {value!r}") from exc
    if not 0 <= rating <= 10000:
        raise ValueError(f"レーティングが想定範囲外です: {rating}")
    return rating


def aggregate_ratings(ratings: Iterable[int]) -> list[dict[str, int]]:
    counts = Counter(ratings)
    cumulative = 0
    rows: list[dict[str, int]] = []
    for rating in sorted(counts, reverse=True):
        count = counts[rating]
        rows.append(
            {
                "rating": rating,
                "count": count,
                "rank": cumulative + 1,
                "usersAtOrAbove": cumulative + count,
            }
        )
        cumulative += count
    return rows


def validate_snapshot(
    ratings: list[int],
    reported_total: int,
    previous_total: int | None,
    max_total_change_ratio: float,
) -> list[str]:
    errors: list[str] = []
    count = len(ratings)
    if count == 0:
        errors.append("取得件数が0件です")
    if reported_total <= 0:
        errors.append(f"サーバー報告総数が不正です: {reported_total}")
    if count != reported_total:
        errors.append(f"取得件数({count})とサーバー報告総数({reported_total})が一致しません")
    if previous_total and previous_total > 0:
        change = abs(count - previous_total) / previous_total
        if change > max_total_change_ratio:
            errors.append(
                f"前回比の人数変化が許容値を超えました: {change:.1%} "
                f"(前回={previous_total}, 今回={count})"
            )
    if ratings and len(set(ratings)) == 1 and count >= 100:
        errors.append("100件以上の全レーティングが同一であり、応答異常の可能性があります")
    return errors


def read_previous_total(path: Path) -> int | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        total = value.get("totalUsers")
        return int(total) if total is not None else None
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        return None


def atomic_write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        "w",
        encoding="utf-8",
        newline="\n",
        dir=path.parent,
        delete=False,
        suffix=".tmp",
    ) as handle:
        json.dump(value, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    temporary.replace(path)


def collect(args: argparse.Namespace) -> tuple[list[int], int, int]:
    ratings: list[int] = []
    pages = 0
    reported_total = 0
    client = QuestRankingClient(
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        gtype=args.gtype,
        category=args.category,
        api_key=args.api_key,
        env=args.env,
        vendor_id=random_vendor_id(),
        command_id_override=args.command_id,
        verbose=args.verbose,
    )

    with client:
        page_number = 0
        while True:
            page = client.fetch_page(page_number, retries=args.retries)
            reported_total = page.total
            for entry in page.users:
                _name, rating_text = parse_user_entry(entry)
                ratings.append(parse_rating(rating_text))

            pages += 1
            LOGGER.info(
                "page=%d users=%d collected=%d total=%d",
                page.page,
                len(page.users),
                len(ratings),
                page.total,
            )
            reached_end = not page.users or page.start + len(page.users) >= page.total
            if reached_end:
                break
            if args.max_pages is not None and pages >= args.max_pages:
                break
            page_number += 1
            time.sleep(args.delay + random.uniform(0, args.jitter))

    return ratings, reported_total, pages


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=ROOT / "public/data/latest.json")
    parser.add_argument("--status-output", type=Path, default=ROOT / "public/data/status.json")
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--gtype", default="shogi")
    parser.add_argument("--category", default="rating")
    parser.add_argument("--api-key", default=DEFAULT_RANKING_API_KEY)
    parser.add_argument("--command-id")
    parser.add_argument("--env", default=DEFAULT_ENV)
    parser.add_argument("--timeout", type=float, default=15)
    parser.add_argument("--delay", type=float, default=1)
    parser.add_argument("--jitter", type=float, default=0.2)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument("--max-pages", type=int)
    parser.add_argument("--max-total-change-ratio", type=float, default=0.25)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    started = datetime.now(timezone.utc)
    next_update = next_scheduled_update(started)

    try:
        ratings, reported_total, pages = collect(args)
        if args.max_pages is not None:
            LOGGER.info("疎通確認完了: %dページ。公開データは更新しません", pages)
            return 0

        previous_total = read_previous_total(args.output)
        errors = validate_snapshot(
            ratings,
            reported_total,
            previous_total,
            args.max_total_change_ratio,
        )
        if errors:
            raise RuntimeError("品質検証に失敗しました: " + " / ".join(errors))

        finished = datetime.now(timezone.utc)
        snapshot = {
            "schemaVersion": 1,
            "source": "Shogi Quest - 実戦！詰めチャレ",
            "collectedAt": iso_utc(finished),
            "collectionStartedAt": iso_utc(started),
            "totalUsers": len(ratings),
            "ratings": aggregate_ratings(ratings),
        }
        status = {
            "schemaVersion": 1,
            "lastAttemptAt": iso_utc(finished),
            "lastAttemptSucceeded": True,
            "lastSuccessfulUpdateAt": iso_utc(finished),
            "nextScheduledUpdateAt": iso_utc(next_update),
        }
        if args.dry_run:
            LOGGER.info("dry-run: 検証成功。ファイルは更新しません")
        else:
            atomic_write_json(args.output, snapshot)
            atomic_write_json(args.status_output, status)
        return 0
    except Exception as exc:
        failed = datetime.now(timezone.utc)
        previous_success: str | None = None
        if args.status_output.exists():
            try:
                previous = json.loads(args.status_output.read_text(encoding="utf-8"))
                previous_success = previous.get("lastSuccessfulUpdateAt")
            except (OSError, json.JSONDecodeError):
                pass
        status = {
            "schemaVersion": 1,
            "lastAttemptAt": iso_utc(failed),
            "lastAttemptSucceeded": False,
            "lastSuccessfulUpdateAt": previous_success,
            "nextScheduledUpdateAt": iso_utc(next_update),
            "errorSummary": str(exc)[:300],
        }
        if not args.dry_run:
            atomic_write_json(args.status_output, status)
        LOGGER.exception("ランキング更新に失敗しました")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
