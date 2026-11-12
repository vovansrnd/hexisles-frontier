import json
import unittest

from isles_core import Action, GamePhase, HexGame, Resource, StructureType
from mods_pack import TradeRoutesMod, _productive_tile
from server_local_v4 import action_params_match, make_game, state_for


class TradeRoutesIntegrationTests(unittest.TestCase):
    def _game(self, routes=True):
        game = make_game(
            "ROUTETEST",
            {
                "points_to_win": 12,
                "fog_of_war": True,
                "fast_start": False,
                "friendly_raider": True,
                "blight": False,
                "routes": routes,
                "bot_difficulty": "hard",
            },
            seed=1234,
        )
        game._add_player_internal("One", False)
        game._add_player_internal("Two", False)
        return game

    def test_routes_plugin_is_installed_only_when_selected(self):
        enabled = self._game(routes=True)
        disabled = self._game(routes=False)
        self.assertTrue(any(isinstance(plugin, TradeRoutesMod) for plugin in enabled._plugins))
        self.assertFalse(any(isinstance(plugin, TradeRoutesMod) for plugin in disabled._plugins))

    def test_routes_setting_reaches_observation_and_active_routes_are_summarized(self):
        game = self._game(routes=True)
        game.players[0].ext["active_routes"] = [{
            "start_vid": 2,
            "end_vid": 8,
            "edge_path": [3, 4, 5],
            "trade_resource": "grain",
            "suppressed_until": 7,
            "income_eligible_turn": 3,
        }]
        state = state_for(game, 0)
        self.assertTrue(state["rules"]["routes"])
        self.assertEqual(state["me"]["active_routes"], [{
            "start_vid": 2,
            "end_vid": 8,
            "edge_count": 3,
            "trade_resource": "grain",
            "suppressed_until": 7,
            "activated_turn": 0,
            "income_eligible_turn": 3,
            "last_income_turn": -1,
            "edge_path": [3, 4, 5],
        }])


    def test_route_action_can_be_generated_activated_and_reported(self):
        mod = TradeRoutesMod()
        game = HexGame("ROUTEPATH", seed=13, fog_of_war=False, plugins=[mod])
        game._add_player_internal("One", False)
        game._add_player_internal("Two", False)
        game.start_game()
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        game.global_turn = 3
        player = game.players[0]
        player.resources[Resource.WOOL] = 5
        player.resources[Resource.GRAIN] = 5

        adjacent = {vid: [] for vid in game.map.vertices}
        for eid, edge in game.map.edges.items():
            a, b = edge.vertices
            adjacent[a].append((b, eid))
            adjacent[b].append((a, eid))

        found = None
        for start in sorted(adjacent):
            stack = [(start, [start], [])]
            while stack:
                cur, vertices, edges = stack.pop()
                if 2 <= len(edges) <= 6:
                    resources = {
                        game.map.hexes[hid].resource
                        for eid in edges
                        for hid in game.map.edges[eid].adjacent_hexes
                        if _productive_tile(game, hid)
                    }
                    if resources:
                        found = (vertices, edges)
                        break
                if len(edges) >= 6:
                    continue
                for nxt, eid in adjacent[cur]:
                    if nxt not in vertices:
                        stack.append((nxt, vertices + [nxt], edges + [eid]))
            if found:
                break
        self.assertIsNotNone(found, "expected a productive map path of 2-6 roads")
        vertices, edges = found
        for vid in (vertices[0], vertices[-1]):
            game.map.vertices[vid].structure_owner = 0
            game.map.vertices[vid].structure_type = StructureType.OUTPOST
        for eid in edges:
            game.map.edges[eid].route_owner = 0
        game.state_version += 1
        game._cache.clear()

        options = mod.enumerate_actions(game, 0)
        chosen = next((a for a in options if tuple(a.p.get("edge_path", ())) == tuple(edges)), None)
        self.assertIsNotNone(chosen, "module should offer the owned road path")
        # This is the actual browser/server wire cycle: Action.to_dict() is
        # JSON encoded, and a tuple edge_path comes back as a JSON list.
        wire_action = json.loads(json.dumps(chosen.to_dict()))
        legal_again = game.enumerate_legal_actions(0)
        accepted = next(
            (
                action for action in legal_again
                if action.type == wire_action["type"]
                and action_params_match(action.p, wire_action["params"])
            ),
            None,
        )
        self.assertIsNotNone(accepted, "JSON round-trip of a legal route action must still match")
        result = game.apply_action(0, accepted)
        self.assertTrue(result.success, result.error)
        self.assertEqual(len(player.ext["active_routes"]), 1)
        self.assertEqual(player.resources[Resource.WOOL], 4)
        self.assertEqual(player.resources[Resource.GRAIN], 4)

    def test_route_income_rotates_fairly_and_skips_suppressed_route(self):
        mod = TradeRoutesMod(one_income_per_turn=True)
        game = HexGame("ROUTEROUNDROBIN", seed=9, plugins=[mod], fog_of_war=False)
        game._add_player_internal("One", False)
        game._add_player_internal("Two", False)
        game.start_game()
        player = game.players[0]
        player.ext["active_routes"] = [
            {"edge_path": [1, 2], "trade_resource": "grain", "activated_turn": 1,
             "income_eligible_turn": 2, "suppressed_until": -1, "last_income_turn": -1},
            {"edge_path": [3, 4], "trade_resource": "ore", "activated_turn": 1,
             "income_eligible_turn": 2, "suppressed_until": -1, "last_income_turn": -1},
            {"edge_path": [5, 6], "trade_resource": "wool", "activated_turn": 1,
             "income_eligible_turn": 2, "suppressed_until": -1, "last_income_turn": -1},
        ]
        # First tick pays the lexically first ready route; next ticks rotate.
        for turn in (10, 11, 12):
            game.global_turn = turn
            mod.on_turn_started(game, 0)
        self.assertEqual(player.resources[Resource.GRAIN], 1)
        self.assertEqual(player.resources[Resource.ORE], 1)
        self.assertEqual(player.resources[Resource.WOOL], 1)
        self.assertEqual(game.ext["mod_telemetry"]["routes"]["counters"]["route_income_units"], 3)

        # The next route in line is grain again, but if it's suppressed, skip it.
        player.ext["active_routes"][0]["suppressed_until"] = 20
        game.global_turn = 13
        before = dict(player.resources)
        mod.on_turn_started(game, 0)
        self.assertEqual(player.resources[Resource.GRAIN], before[Resource.GRAIN])
        self.assertEqual(sum(player.resources.values()) - sum(before.values()), 1)

    def test_current_raider_marker_is_public_without_revealing_hidden_hex(self):
        game = self._game(routes=False)
        self.assertTrue(game.start_game().success)
        player = game.players[0]
        hidden_id = next(
            hid for hid in sorted(game.map.hexes)
            if hid not in player.revealed_hexes
        )
        for tile in game.map.hexes.values():
            tile.has_raider = False
        game.map.hexes[hidden_id].has_raider = True

        state = state_for(game, 0)
        view = next(item for item in state["hexes"] if item["hex_id"] == hidden_id)
        self.assertFalse(view["revealed"])
        self.assertEqual(view["terrain"], "unknown")
        self.assertEqual(view["number_token"], 0)
        self.assertTrue(view["has_raider"], "current raider position must be visible under fog")
        self.assertNotIn("is_ruins", view, "hidden terrain attributes must remain hidden")

    def test_routes_default_off_is_backward_compatible(self):
        game = make_game("ROUTEDEFAULT", {"blight": False}, seed=1234)
        self.assertFalse(any(isinstance(plugin, TradeRoutesMod) for plugin in game._plugins))
        self.assertFalse(game.ext["local_server_config"].get("routes", False))


if __name__ == "__main__":
    unittest.main()
