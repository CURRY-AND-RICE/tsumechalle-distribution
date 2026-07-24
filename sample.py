#!/usr/bin/env python3
"""
将棋クエストの公開ランキングを、観測済みの平文TCPプロトコルで取得してCSVへ保存する。

注意:
- 非公式クライアントです。サービスの利用規約・運用方針を確認してください。
- デフォルトでは1ページごとに約1秒待ち、サーバー負荷を抑えます。
- ランキングは取得中にも変動するため、厳密な同一時点スナップショットにはなりません。
- 認証情報やログイントークンは使用しません。
"""

from __future__ import annotations

import argparse
import csv
import json
import random
import socket
import string
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO


DEFAULT_HOST = "ec2-54-229-59-237.eu-west-1.compute.amazonaws.com"
DEFAULT_PORT = 4000

# start応答のapiマップ内で、ランキング取得に対応していたキー。
# 右辺の8桁コマンドIDは接続時にサーバーから動的に取得する。
DEFAULT_RANKING_API_KEY = "wIQ04iJvKlim4iJ"
# 2026-07-24のキャプチャで確認したランキング取得コマンドID。
# start応答にapiマップがない場合のフォールバックとして使用する。
DEFAULT_RANKING_COMMAND_ID = "e9baa8e2"

# 2026-07-24のキャプチャで観測したenv。秘密情報ではない。
DEFAULT_ENV = "flutter:2.7.4+571:sdk_gphone16k_x86_64:android:17:shogi"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def compact_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def random_vendor_id(length: int = 10) -> str:
    alphabet = string.ascii_lowercase + string.digits
    return "".join(random.SystemRandom().choice(alphabet) for _ in range(length))


def parse_protocol_line(raw: bytes) -> tuple[str, dict[str, Any] | None, str]:
    """
    '<command> <json>\\n' を解析する。
    JSONでない行も診断できるよう、元の文字列も返す。
    """
    text = raw.decode("utf-8", errors="replace").rstrip("\r\n")
    if " " not in text:
        return text, None, text

    command, payload_text = text.split(" ", 1)
    try:
        payload = json.loads(payload_text)
    except json.JSONDecodeError:
        return command, None, text

    if not isinstance(payload, dict):
        return command, None, text
    return command, payload, text


def parse_user_entry(entry: str) -> tuple[str, str]:
    """
    観測形式:
        プレイヤー名//レーティング/
    後ろから分割し、名前に予期しない記号があっても可能な限り保持する。
    """
    value = entry.strip()
    if value.endswith("/"):
        value = value[:-1]

    if "//" not in value:
        raise ValueError(f"未知のユーザー表現です: {entry!r}")

    name, rating = value.rsplit("//", 1)
    name = name.strip()
    rating = rating.strip()

    if not name or not rating:
        raise ValueError(f"名前またはレーティングが空です: {entry!r}")
    return name, rating


@dataclass
class RankingPage:
    page: int
    start: int
    total: int
    users: list[str]


class QuestRankingClient:
    def __init__(
        self,
        host: str,
        port: int,
        timeout: float,
        gtype: str,
        category: str,
        api_key: str,
        env: str,
        vendor_id: str,
        command_id_override: str | None,
        verbose: bool,
    ) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.gtype = gtype
        self.category = category
        self.api_key = api_key
        self.env = env
        self.vendor_id = vendor_id
        self.command_id_override = command_id_override
        self.verbose = verbose

        self.sock: socket.socket | None = None
        self.reader: BinaryIO | None = None
        self.ranking_command_id: str | None = None

    def close(self) -> None:
        if self.reader is not None:
            try:
                self.reader.close()
            except OSError:
                pass
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
        self.reader = None
        self.sock = None
        self.ranking_command_id = None

    def __enter__(self) -> "QuestRankingClient":
        return self

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        self.close()

    def _send(self, command: str, payload: dict[str, Any]) -> None:
        if self.sock is None:
            raise ConnectionError("未接続です。")
        line = f"{command} {compact_json(payload)}\n".encode("utf-8")
        if self.verbose:
            print(f"> {command} {compact_json(payload)}", file=sys.stderr)
        self.sock.sendall(line)

    def _wait_for(
        self,
        expected_command: str,
        expected_page: int | None = None,
    ) -> dict[str, Any]:
        if self.sock is None or self.reader is None:
            raise ConnectionError("未接続です。")

        deadline = time.monotonic() + self.timeout

        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"{expected_command!r} の応答待ちがタイムアウトしました。"
                )

            self.sock.settimeout(remaining)
            raw = self.reader.readline()
            if raw == b"":
                raise ConnectionError("サーバーが接続を終了しました。")

            command, payload, text = parse_protocol_line(raw)

            if self.verbose:
                preview = text if len(text) <= 300 else text[:300] + "..."
                print(f"< {preview}", file=sys.stderr)

            # 接続数などのプッシュ通知は無視する。
            if command != expected_command or payload is None:
                continue

            if expected_page is not None:
                try:
                    response_page = int(payload.get("page"))
                except (TypeError, ValueError):
                    continue
                if response_page != expected_page:
                    continue

            return payload

    def connect(self) -> None:
        self.close()

        sock = socket.create_connection(
            (self.host, self.port),
            timeout=self.timeout,
        )
        sock.settimeout(self.timeout)
        self.sock = sock
        self.reader = sock.makefile("rb")

        start_payload = {
            "gtype": self.gtype,
            "vendor_id": self.vendor_id,
            "env": self.env,
            "lang": "ja",
        }
        self._send("start", start_payload)
        response = self._wait_for("start")

        if self.command_id_override:
            command_id = self.command_id_override
        else:
            api_map = response.get("api")
            command_id = (
                api_map.get(self.api_key)
                if isinstance(api_map, dict)
                else None
            )

            if not isinstance(command_id, str) or len(command_id) != 8:
                # 実サーバーがapiマップを省略したstart応答を返す場合がある。
                # キャプチャで確認済みのIDへフォールバックする。
                command_id = DEFAULT_RANKING_COMMAND_ID
                response_preview = compact_json(response)
                if len(response_preview) > 500:
                    response_preview = response_preview[:500] + "..."
                print(
                    "警告: start応答からランキング用コマンドIDを取得できませんでした。"
                    f" 確認済みID {command_id} を使用します。"
                    f" start応答={response_preview}",
                    file=sys.stderr,
                )

        self.ranking_command_id = command_id
        print(
            f"接続しました: {self.host}:{self.port} "
            f"(ranking command={command_id})",
            file=sys.stderr,
        )

    def fetch_page_once(self, page: int) -> RankingPage:
        if self.sock is None or self.ranking_command_id is None:
            self.connect()

        assert self.ranking_command_id is not None

        request = {
            "gtype": self.gtype,
            "page": page,
            "category": self.category,
        }
        self._send(self.ranking_command_id, request)
        response = self._wait_for(self.ranking_command_id, expected_page=page)

        if response.get("gtype") != self.gtype:
            raise ValueError(f"gtypeが一致しません: {response.get('gtype')!r}")
        if response.get("category") != self.category:
            raise ValueError(
                f"categoryが一致しません: {response.get('category')!r}"
            )

        users = response.get("users")
        if not isinstance(users, list) or not all(isinstance(x, str) for x in users):
            raise ValueError("usersが文字列配列ではありません。")

        try:
            start = int(response["start"])
            total = int(response["total"])
            response_page = int(response["page"])
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError(f"ランキング応答の数値フィールドが不正です: {exc}") from exc

        return RankingPage(
            page=response_page,
            start=start,
            total=total,
            users=users,
        )

    def fetch_page(self, page: int, retries: int) -> RankingPage:
        last_error: Exception | None = None

        for attempt in range(retries + 1):
            try:
                return self.fetch_page_once(page)
            except (OSError, TimeoutError, ConnectionError, ValueError, RuntimeError) as exc:
                last_error = exc
                self.close()

                if attempt >= retries:
                    break

                wait = min(30.0, 2.0 ** attempt)
                print(
                    f"page={page} の取得に失敗しました: {exc} "
                    f"({wait:.1f}秒後に再試行)",
                    file=sys.stderr,
                )
                time.sleep(wait)

        assert last_error is not None
        raise RuntimeError(
            f"page={page} を {retries + 1} 回試行しましたが取得できませんでした。"
        ) from last_error


def state_path_for(output: Path) -> Path:
    return output.with_name(output.name + ".state.json")


def load_state(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    with path.open("r", encoding="utf-8") as f:
        value = json.load(f)
    if not isinstance(value, dict):
        raise ValueError(f"状態ファイルが不正です: {path}")
    return value


def save_state(path: Path, state: dict[str, Any]) -> None:
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)
        f.write("\n")
    temporary.replace(path)


def read_existing_names(output: Path) -> set[str]:
    names: set[str] = set()
    if not output.exists() or output.stat().st_size == 0:
        return names

    with output.open("r", encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            name = row.get("name")
            if name:
                names.add(name)
    return names


def validate_resume_state(
    state: dict[str, Any],
    args: argparse.Namespace,
) -> None:
    expected = {
        "host": args.host,
        "port": args.port,
        "gtype": args.gtype,
        "category": args.category,
    }
    for key, value in expected.items():
        old = state.get(key)
        if old != value:
            raise ValueError(
                f"状態ファイルの{key}={old!r}が今回の指定{value!r}と一致しません。"
                " 別の出力ファイルを使うか --overwrite を指定してください。"
            )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="将棋クエストの公開ランキングをCSVへ保存します。",
    )
    parser.add_argument("--output", type=Path, default=Path("shogi_ranking.csv"))
    parser.add_argument("--host", default=DEFAULT_HOST)
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--gtype", default="shogi")
    parser.add_argument("--category", default="rating")
    parser.add_argument("--api-key", default=DEFAULT_RANKING_API_KEY)
    parser.add_argument(
        "--command-id",
        help="start応答のapiマップを使わず、8桁コマンドIDを明示指定します。通常は不要です。",
    )
    parser.add_argument("--env", default=DEFAULT_ENV)
    parser.add_argument("--vendor-id", default=random_vendor_id())
    parser.add_argument("--timeout", type=float, default=15.0)
    parser.add_argument("--delay", type=float, default=1.0)
    parser.add_argument("--jitter", type=float, default=0.2)
    parser.add_argument("--retries", type=int, default=4)
    parser.add_argument(
        "--max-pages",
        type=int,
        help="動作確認用。指定ページ数を取得したら途中終了します。",
    )
    parser.add_argument(
        "--overwrite",
        action="store_true",
        help="既存CSVと状態ファイルを削除して最初から取得します。",
    )
    parser.add_argument("--verbose", action="store_true")
    return parser


def main() -> int:
    args = build_parser().parse_args()

    if not (1 <= args.port <= 65535):
        print("portは1～65535で指定してください。", file=sys.stderr)
        return 2
    if args.timeout <= 0:
        print("timeoutは正の値で指定してください。", file=sys.stderr)
        return 2
    if args.delay < 0.25:
        print(
            "サーバー負荷を避けるため、delayは0.25秒以上にしてください。",
            file=sys.stderr,
        )
        return 2
    if args.jitter < 0:
        print("jitterは0以上で指定してください。", file=sys.stderr)
        return 2
    if args.retries < 0:
        print("retriesは0以上で指定してください。", file=sys.stderr)
        return 2
    if args.command_id is not None:
        value = args.command_id.lower()
        if len(value) != 8 or any(c not in string.hexdigits for c in value):
            print("command-idは8桁の16進数で指定してください。", file=sys.stderr)
            return 2
        args.command_id = value

    output: Path = args.output.resolve()
    state_path = state_path_for(output)
    output.parent.mkdir(parents=True, exist_ok=True)

    if args.overwrite:
        output.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)

    state = load_state(state_path)

    if state is not None:
        validate_resume_state(state, args)
        if state.get("completed") is True:
            print(
                f"取得済みです: {output}\n"
                "最初から再取得する場合は --overwrite を指定してください。",
                file=sys.stderr,
            )
            return 0
        next_page = int(state.get("next_page", 0))
        rows_written = int(state.get("rows_written", 0))
        print(
            f"中断地点から再開します: page={next_page}, rows={rows_written}",
            file=sys.stderr,
        )
    else:
        if output.exists() and output.stat().st_size > 0:
            print(
                f"既存CSVに対応する状態ファイルがありません: {output}\n"
                "別名を使うか --overwrite を指定してください。",
                file=sys.stderr,
            )
            return 2
        next_page = 0
        rows_written = 0

    existing_names = read_existing_names(output)
    duplicate_names = 0
    pages_fetched_this_run = 0

    csv_exists = output.exists() and output.stat().st_size > 0
    csv_file = output.open(
        "a",
        encoding="utf-8-sig",
        newline="",
    )
    writer = csv.DictWriter(csv_file, fieldnames=["rank", "name", "rating"])
    if not csv_exists:
        writer.writeheader()
        csv_file.flush()

    completed = False
    last_total: int | None = None

    client = QuestRankingClient(
        host=args.host,
        port=args.port,
        timeout=args.timeout,
        gtype=args.gtype,
        category=args.category,
        api_key=args.api_key,
        env=args.env,
        vendor_id=args.vendor_id,
        command_id_override=args.command_id,
        verbose=args.verbose,
    )

    try:
        with client:
            page_number = next_page

            while True:
                page = client.fetch_page(page_number, retries=args.retries)
                last_total = page.total

                parsed_rows: list[dict[str, Any]] = []
                for offset, entry in enumerate(page.users):
                    name, rating = parse_user_entry(entry)
                    rank = page.start + offset + 1

                    if name in existing_names:
                        duplicate_names += 1
                    existing_names.add(name)

                    parsed_rows.append(
                        {
                            "rank": rank,
                            "name": name,
                            "rating": rating,
                        }
                    )

                writer.writerows(parsed_rows)
                csv_file.flush()

                rows_written += len(parsed_rows)
                pages_fetched_this_run += 1
                page_number += 1

                reached_end = (
                    len(page.users) == 0
                    or page.start + len(page.users) >= page.total
                )

                state_value = {
                    "version": 1,
                    "host": args.host,
                    "port": args.port,
                    "gtype": args.gtype,
                    "category": args.category,
                    "output": str(output),
                    "next_page": page_number,
                    "last_page": page.page,
                    "rows_written": rows_written,
                    "last_total": page.total,
                    "completed": reached_end,
                    "updated_at_utc": utc_now(),
                }
                save_state(state_path, state_value)

                percent = (
                    100.0
                    if page.total <= 0
                    else min(100.0, (page.start + len(page.users)) * 100.0 / page.total)
                )
                print(
                    f"page={page.page:4d} "
                    f"rank={page.start + 1:5d}-"
                    f"{page.start + len(page.users):5d} "
                    f"total={page.total:5d} "
                    f"{percent:6.2f}%",
                    file=sys.stderr,
                )

                if reached_end:
                    completed = True
                    break

                if (
                    args.max_pages is not None
                    and pages_fetched_this_run >= args.max_pages
                ):
                    print(
                        "--max-pagesに達したため停止しました。"
                        "同じコマンドで再開できます。",
                        file=sys.stderr,
                    )
                    break

                time.sleep(args.delay + random.uniform(0.0, args.jitter))

    except KeyboardInterrupt:
        print(
            "\n中断しました。状態ファイルから同じコマンドで再開できます。",
            file=sys.stderr,
        )
        return 130
    except Exception as exc:
        print(f"\n取得に失敗しました: {exc}", file=sys.stderr)
        print(
            f"状態ファイルがあれば、同じコマンドで再開できます: {state_path}",
            file=sys.stderr,
        )
        return 1
    finally:
        csv_file.close()

    if completed:
        print(
            f"\n完了: {output}\n"
            f"CSV行数: {rows_written}, サーバー報告総数: {last_total}, "
            f"重複名検出数: {duplicate_names}",
            file=sys.stderr,
        )
        if duplicate_names:
            print(
                "取得中の順位変動などにより、同じ名前が複数ページに現れた可能性があります。",
                file=sys.stderr,
            )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
