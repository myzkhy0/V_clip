"""
register_channel.py - Manual channel registration utility.

Use this tool to add / inspect / enable / disable tracked channels without
editing source code keywords.
"""

from __future__ import annotations

import argparse
import re
from urllib.parse import urlparse

from config import GROUP_KEYWORDS
from db import execute, fetchall
from youtube_client import get_uploads_playlist_id, resolve_channel_identifier


def _normalize_identifier(raw: str) -> str:
    value = (raw or "").strip()
    if not value:
        raise ValueError("identifier is empty")

    if value.startswith("UC"):
        return value
    if value.startswith("@"):
        return value
    if "youtube.com" not in value and "youtu.be" not in value:
        return value

    parsed = urlparse(value)
    path = parsed.path.strip("/")
    if not path:
        return value

    if path.startswith("@"):
        return path

    m = re.match(r"(channel|c|user)/([^/]+)", path, flags=re.IGNORECASE)
    if m:
        return m.group(2)

    return value


def _infer_group(text: str) -> str:
    haystack = (text or "").lower()
    for group_name, keywords in GROUP_KEYWORDS.items():
        for keyword in keywords:
            if keyword.lower() in haystack:
                return group_name
    return "other"


def cmd_add(args: argparse.Namespace) -> int:
    raw_identifier = args.identifier.strip()
    identifier = _normalize_identifier(raw_identifier)
    channel_id = resolve_channel_identifier(identifier)

    channel_name = (args.name or "").strip()
    if not channel_name:
        channel_name = raw_identifier

    group_name = (args.group or "").strip()
    if not group_name:
        group_name = _infer_group(f"{raw_identifier} {channel_name}")

    uploads_playlist_id = get_uploads_playlist_id(channel_id) or ""

    execute(
        """
        INSERT INTO channels
            (channel_id, channel_name, group_name, uploads_playlist_id, is_tracked, empty_streak, paused_until)
        VALUES
            (%s, %s, %s, %s, TRUE, 0, NULL)
        ON CONFLICT (channel_id) DO UPDATE SET
            channel_name = EXCLUDED.channel_name,
            group_name = EXCLUDED.group_name,
            uploads_playlist_id = CASE
                WHEN COALESCE(channels.uploads_playlist_id, '') = '' THEN EXCLUDED.uploads_playlist_id
                ELSE channels.uploads_playlist_id
            END,
            is_tracked = TRUE,
            paused_until = NULL
        """,
        (channel_id, channel_name, group_name, uploads_playlist_id),
    )

    print("[OK] Channel registered.")
    print(f"  channel_id: {channel_id}")
    print(f"  name      : {channel_name}")
    print(f"  group     : {group_name}")
    print("  tracked   : TRUE")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    where_clause = "WHERE is_tracked = TRUE" if args.tracked_only else ""
    rows = fetchall(
        f"""
        SELECT
            channel_id,
            channel_name,
            group_name,
            is_tracked,
            COALESCE(empty_streak, 0) AS empty_streak,
            paused_until
        FROM channels
        {where_clause}
        ORDER BY is_tracked DESC, channel_name ASC
        """
    )
    if not rows:
        print("No channels found.")
        return 0

    for row in rows:
        paused_until = row["paused_until"] if row["paused_until"] else "-"
        tracked = "Y" if row["is_tracked"] else "N"
        print(
            f"[{tracked}] {row['channel_name']} | {row['group_name']} | "
            f"empty={row['empty_streak']} | paused_until={paused_until} | {row['channel_id']}"
        )
    print(f"Total: {len(rows)}")
    return 0


def cmd_set_tracked(args: argparse.Namespace) -> int:
    execute(
        """
        UPDATE channels
        SET is_tracked = %s,
            paused_until = NULL,
            empty_streak = CASE WHEN %s THEN 0 ELSE empty_streak END
        WHERE channel_id = %s
        """,
        (args.enabled, args.enabled, args.channel_id.strip()),
    )
    state = "TRUE" if args.enabled else "FALSE"
    print(f"[OK] is_tracked set to {state} for {args.channel_id.strip()}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Manual channel registration utility")
    sub = parser.add_subparsers(dest="command", required=True)

    p_add = sub.add_parser("add", help="register or upsert a channel")
    p_add.add_argument("identifier", help="channel id / @handle / youtube channel URL")
    p_add.add_argument("--name", help="display name (optional)")
    p_add.add_argument("--group", help="group name (optional, default: auto/other)")
    p_add.set_defaults(func=cmd_add)

    p_list = sub.add_parser("list", help="list channels")
    p_list.add_argument("--tracked-only", action="store_true", help="show tracked channels only")
    p_list.set_defaults(func=cmd_list)

    p_set = sub.add_parser("set-tracked", help="enable/disable channel tracking")
    p_set.add_argument("channel_id", help="target channel id")
    p_set.add_argument("--enabled", action="store_true", help="set is_tracked = TRUE")
    p_set.add_argument("--disabled", action="store_true", help="set is_tracked = FALSE")
    p_set.set_defaults(func=cmd_set_tracked)

    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()

    if args.command == "set-tracked":
        if args.enabled == args.disabled:
            parser.error("set-tracked requires exactly one of --enabled / --disabled")
        args.enabled = bool(args.enabled and not args.disabled)

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())

