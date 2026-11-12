import unittest
from pathlib import Path

import server_local_v4
from isles_core import GamePhase, HexGame
from mods_pack import TradeRoutesMod

ROOT = Path(__file__).resolve().parents[1]


class GameUiFeaturesTests(unittest.TestCase):
    def test_finished_observation_reveals_final_hidden_victory_points(self):
        game = HexGame("FINALUI", points_to_win=20, seed=444, fog_of_war=True)
        game._add_player_internal("One", False)
        game._add_player_internal("Two", False)
        self.assertTrue(game.start_game().success)
        game.players[0].hidden_vp = 2
        game.players[1].hidden_vp = 3
        game.phase = GamePhase.FINISHED
        game.winner_id = 0
        game._cache.clear()
        state = game.get_state_for_player(0)
        observed = {int(player["player_id"]): int(player["victory_points"]) for player in state["players"]}
        self.assertEqual(observed[0], game.get_victory_points(0, public=False))
        self.assertEqual(observed[1], game.get_victory_points(1, public=False))
        self.assertGreater(observed[1], game.get_victory_points(1, public=True))

    def test_server_uses_required_game_defaults_and_valid_mod_options(self):
        game = server_local_v4.make_game("DEFAULTS", {}, seed=24)
        config = game.ext["local_server_config"]
        self.assertEqual(config["points_to_win"], 12)
        self.assertTrue(game.fog_of_war)
        self.assertTrue(game.friendly_raider)
        self.assertFalse(game.fast_start)
        self.assertTrue(config["blight"])
        self.assertFalse(config["routes"])
        self.assertFalse(any(isinstance(plugin, TradeRoutesMod) for plugin in game._plugins))
        routes_game = server_local_v4.make_game("ROUTES", {"routes": True, "blight": False}, seed=25)
        self.assertTrue(any(isinstance(plugin, TradeRoutesMod) for plugin in routes_game._plugins))


if __name__ == "__main__":
    unittest.main()
