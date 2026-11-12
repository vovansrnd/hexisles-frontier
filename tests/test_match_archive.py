import tempfile
import unittest
from pathlib import Path

from isles_core import GamePhase, HexGame
from match_archive import MatchArchive, MatchSession, source_fingerprints


class MatchArchiveTests(unittest.TestCase):
    def _finished_package(self):
        game = HexGame("ARCHIVETEST", points_to_win=12, seed=12345, fog_of_war=True)
        game._add_player_internal("Local Test", False)
        game.add_bot("🤖 Test Bot")
        game.bot_difficulty = "hard"
        game.ext["bot_types"] = {"1": "c3po"}
        game.ext["local_server_config"] = {
            "points_to_win": 12,
            "fog_of_war": True,
            "bot_difficulty": "hard",
        }
        self.assertTrue(game.start_game().success)
        session = MatchSession(game, game.ext["local_server_config"])

        actor = game.current_player_id
        legal = game.enumerate_legal_actions(actor)
        action = legal[0]
        result = game.apply_action(actor, action)
        session.record(
            game,
            event_type="action",
            actor_id=actor,
            action_type=action.type,
            params=action.p,
            success=result.success,
            error=result.error,
            legal_actions=legal,
            decision={"reason": "unit test", "action": action.type},
        )
        game.phase = GamePhase.FINISHED
        game.winner_id = 0
        session.record(game, event_type="match_finished", success=True)
        return session.package(game)

    def test_persists_completed_match_and_deduplicates(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = MatchArchive(Path(tmp) / "matches.sqlite3")
            package = self._finished_package()
            self.assertEqual(archive.stats()["matches"], 0)
            self.assertTrue(archive.save_completed_match(package))
            self.assertFalse(archive.save_completed_match(package))
            self.assertEqual(archive.stats()["matches"], 1)
            self.assertEqual(archive.stats()["events"], len(package["events"]))

    def test_export_contains_deltas_decisions_and_initial_board(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = MatchArchive(Path(tmp) / "matches.sqlite3")
            package = self._finished_package()
            archive.save_completed_match(package)
            result = archive.export_match(package["match_id"])
            self.assertIsNotNone(result)
            self.assertGreater(len(result["match"]["initial_state"]["board"]["hexes"]), 0)
            self.assertTrue(any(event["decision"] for event in result["events"]))
            self.assertTrue(any(event["delta"] for event in result["events"]))


    def test_source_fingerprints_identify_runtime_server_and_frontend(self):
        import os
        old = os.environ.get("HEXISLES_STATIC")
        os.environ["HEXISLES_STATIC"] = "static_v5"
        try:
            fingerprints = source_fingerprints()
        finally:
            if old is None:
                os.environ.pop("HEXISLES_STATIC", None)
            else:
                os.environ["HEXISLES_STATIC"] = old
        self.assertRegex(fingerprints["server_local_v4.py"], r"^[0-9a-f]{64}$")
        self.assertRegex(fingerprints["frontend:static_v5"], r"^[0-9a-f]{64}$")
        self.assertNotIn("server_local_v3.py", fingerprints)

    def test_leaderboard_uses_human_players_by_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            archive = MatchArchive(Path(tmp) / "matches.sqlite3")
            archive.save_completed_match(self._finished_package())
            board = archive.leaderboard()
            self.assertEqual(len(board), 1)
            self.assertEqual(board[0]["name"], "Local Test")
            full_board = archive.leaderboard(include_bots=True)
            self.assertEqual(len(full_board), 2)


if __name__ == "__main__":
    unittest.main()
