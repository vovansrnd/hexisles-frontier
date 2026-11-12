#!/usr/bin/env python3
"""Inspect/export the SQLite archive of completed HexIsles matches."""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

from match_archive import MatchArchive

ROOT = Path(__file__).resolve().parent
_setting = os.environ.get("HEXISLES_DB_PATH", "").strip()
DB_PATH = Path(_setting).expanduser() if _setting else ROOT / "data" / "hexisles.sqlite3"
if not DB_PATH.is_absolute():
    DB_PATH = (ROOT / DB_PATH).resolve()


def print_matches(archive: MatchArchive, limit: int) -> None:
    rows = archive.list_matches(limit)
    if not rows:
        print("Завершённых партий пока нет.")
        return
    headers = ("ID", "Дата", "Победитель", "Ходы", "События", "Действия", "Причина")
    data = []
    for row in rows:
        data.append((
            row["match_id"][:10],
            (row["finished_at"] or "")[:19].replace("T", " "),
            row["winner_name"] or "—",
            str(row["global_turns"]),
            str(row["event_count"]),
            str(row["action_count"]),
            row["finish_reason"],
        ))
    widths = [max(len(headers[i]), *(len(str(r[i])) for r in data)) for i in range(len(headers))]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * w for w in widths))
    for row in data:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def print_leaderboard(archive: MatchArchive, limit: int, min_games: int, include_bots: bool) -> None:
    rows = archive.leaderboard(limit, min_games, include_bots)
    if not rows:
        print("Для заданных условий пока нет записей.")
        return
    headers = ("#", "Имя", "Партий", "Побед", "% побед", "Ср. ОП", "Ср. ходов")
    values = []
    for i, row in enumerate(rows, 1):
        values.append((str(i), row["name"], str(row["games"]), str(row["wins"]), f'{row["win_rate"]:.1f}', f'{row["avg_vp"]:.2f}', f'{row["avg_match_turns"]:.2f}'))
    widths = [max(len(headers[i]), *(len(str(r[i])) for r in values)) for i in range(len(headers))]
    print("  ".join(headers[i].ljust(widths[i]) for i in range(len(headers))))
    print("  ".join("-" * w for w in widths))
    for row in values:
        print("  ".join(str(row[i]).ljust(widths[i]) for i in range(len(headers))))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Архив завершённых партий HexIsles")
    parser.add_argument("--db", default=str(DB_PATH), help=f"Путь к SQLite (по умолчанию: {DB_PATH})")
    sub = parser.add_subparsers(dest="command", required=True)

    matches = sub.add_parser("matches", help="Список завершённых партий")
    matches.add_argument("--limit", type=int, default=30)

    leaderboard = sub.add_parser("leaderboard", help="Таблица победителей и статистики")
    leaderboard.add_argument("--limit", type=int, default=30)
    leaderboard.add_argument("--min-games", type=int, default=1)
    leaderboard.add_argument("--include-bots", action="store_true", help="Включить ботов в таблицу")

    summary = sub.add_parser("summary", help="Сводка одной партии")
    summary.add_argument("match_id", help="Полный или начальный фрагмент ID матча")

    export = sub.add_parser("export", help="Экспорт полной партии в JSON")
    export.add_argument("match_id", help="Полный или начальный фрагмент ID матча")
    export.add_argument("-o", "--output", help="Файл результата; по умолчанию match-<id>.json")

    stats = sub.add_parser("stats", help="Общая статистика архива")

    args = parser.parse_args(argv)
    archive = MatchArchive(args.db)

    if args.command == "matches":
        print_matches(archive, args.limit)
    elif args.command == "leaderboard":
        print_leaderboard(archive, args.limit, args.min_games, args.include_bots)
    elif args.command == "stats":
        info = archive.stats()
        print(f"Завершённых партий: {info['matches']}")
        print(f"Событий: {info['events']}")
        print(f"Действий: {info['actions']}")
        print(f"SQLite: {archive.path}")
    elif args.command in ("summary", "export"):
        # Accept abbreviated IDs for convenience, but reject ambiguous prefixes.
        with archive._connection() as conn:
            candidates = conn.execute(
                "SELECT match_id FROM matches WHERE status='completed' AND match_id LIKE ? ORDER BY finished_at DESC LIMIT 2",
                (str(args.match_id) + "%",),
            ).fetchall()
        if not candidates:
            print("Завершённая партия с таким ID не найдена.", file=sys.stderr)
            return 2
        if len(candidates) > 1 and candidates[0]["match_id"] != args.match_id:
            print("Неоднозначный префикс ID; укажи больше символов.", file=sys.stderr)
            return 2
        match_id = candidates[0]["match_id"]
        if args.command == "summary":
            result = archive.match_summary(match_id)
            if not result:
                return 2
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            result = archive.export_match(match_id)
            if not result:
                return 2
            output = Path(args.output) if args.output else Path(f"match-{match_id[:10]}.json")
            output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
            print(f"Экспортировано: {output.resolve()}")
            print(f"Событий: {len(result['events'])}; игроков: {len(result['players'])}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
