"""The live server must wire completed games into the match archive."""
import pytest

from isles_core import GamePhase
from match_archive import MatchArchive
import server_local_v4 as server


def test_server_records_and_persists_a_completed_match(tmp_path, monkeypatch):
    archive = MatchArchive(tmp_path / "live.sqlite3")
    monkeypatch.setenv("HEXISLES_MATCH_ARCHIVE", "1")
    monkeypatch.setenv("HEXISLES_STATIC", "static_v5")
    monkeypatch.setattr(server, "MATCH_ARCHIVE", archive)
    monkeypatch.setattr(server, "MATCH_SESSIONS", {})

    config = {
        "points_to_win": 12,
        "fog_of_war": True,
        "fast_start": False,
        "friendly_raider": True,
        "blight": True,
        "blight_mode": "factions",
        "cult_visibility": "open",
        "blight_spread_chance": 0.4,
        "routes": False,
        "bot_difficulty": "skynet",
    }
    game = server.make_game("ARCHIVEWIRE", config, seed=91423)
    game._add_player_internal("Human probe", False)
    ok, _ = server.add_bot(game, "c3po")
    assert ok
    assert game.start_game().success
    server._start_match_recording("ARCHIVEWIRE", game)
    assert "ARCHIVEWIRE" in server.MATCH_SESSIONS

    actor = game.current_player_id
    legal = game.enumerate_legal_actions(actor)
    assert legal
    action = legal[0]
    result = game.apply_action(actor, action)
    server._record_match_event(
        "ARCHIVEWIRE", game,
        event_type="action",
        actor_id=actor,
        action_type=action.type,
        params=action.p,
        success=result.success,
        error=result.error,
        meta={"source": "test"},
    )

    # Force a deterministic terminal state; the test is about server wiring,
    # not about requiring a full autonomous game to finish.
    game.phase = GamePhase.FINISHED
    game.winner_id = 0
    server._record_match_event(
        "ARCHIVEWIRE", game, event_type="test_finish", success=True,
        meta={"source": "integration_test"},
    )

    assert archive.stats()["matches"] == 1
    rows = archive.list_matches(5)
    assert rows[0]["room_id"] == "ARCHIVEWIRE"
    exported = archive.export_match(rows[0]["match_id"])
    assert exported is not None
    event_types = [event["event_type"] for event in exported["events"]]
    assert "match_started" in event_types
    assert "action" in event_types
    assert "match_finished" in event_types
    assert "ARCHIVEWIRE" not in server.MATCH_SESSIONS
