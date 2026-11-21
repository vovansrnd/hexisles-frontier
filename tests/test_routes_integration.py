import json
import unittest
from types import SimpleNamespace

from isles_core import Action, GamePhase, HexGame, Resource, StructureType
from mods_pack import TradeRoutesMod, _productive_tile
from server_local_v4 import action_params_match, make_game, state_for


class TradeRoutesIntegrationTests(unittest.TestCase):
    def _register_test_routes(self, game, player_id, resources, path_length=2):
        """Install real, disjoint owned-road paths for route lifecycle tests."""
        adjacent = {vid: [] for vid in game.map.vertices}
        for edge_id, edge in game.map.edges.items():
            left, right = edge.vertices
            adjacent[left].append((right, edge_id))
            adjacent[right].append((left, edge_id))
        for neighbours in adjacent.values():
            neighbours.sort()

        used_vertices = set()
        routes = []
        for resource_name in resources:
            found = None
            for start in sorted(adjacent):
                if start in used_vertices:
                    continue
                stack = [(start, [start], [])]
                while stack and found is None:
                    current, vertices, edge_path = stack.pop()
                    if len(edge_path) == path_length:
                        if current != start and start < current and not (
                            set(vertices) & used_vertices
                        ):
                            found = (vertices, edge_path)
                        continue
                    for nxt, edge_id in reversed(adjacent[current]):
                        if nxt in vertices or nxt in used_vertices:
                            continue
                        stack.append((nxt, vertices + [nxt], edge_path + [edge_id]))
                if found:
                    break

            self.assertIsNotNone(
                found, f"expected a disjoint path of {path_length} roads"
            )
            vertices, edge_path = found
            for vertex_id in (vertices[0], vertices[-1]):
                game.map.vertices[vertex_id].structure_owner = player_id
                game.map.vertices[vertex_id].structure_type = StructureType.OUTPOST
            for edge_id in edge_path:
                game.map.edges[edge_id].route_owner = player_id
            used_vertices.update(vertices)
            routes.append(
                {
                    "start_vid": vertices[0],
                    "end_vid": vertices[-1],
                    "edge_path": list(edge_path),
                    "trade_resource": resource_name,
                    "activated_turn": 1,
                    "income_eligible_turn": 2,
                    "suppressed_until": -1,
                    "last_income_turn": -1,
                    "vertices": vertices,
                }
            )
        game.state_version += 1
        game._cache.clear()
        return routes

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
        routes = self._register_test_routes(
            game, 0, ["grain", "ore", "wool"], path_length=2
        )
        player.ext["active_routes"] = routes

        # One shared income slot rotates fairly across all currently ready routes.
        for turn in (10, 11, 12):
            game.global_turn = turn
            mod.on_turn_started(game, 0)
        self.assertEqual(player.resources[Resource.GRAIN], 1)
        self.assertEqual(player.resources[Resource.ORE], 1)
        self.assertEqual(player.resources[Resource.WOOL], 1)
        self.assertEqual(game.ext["mod_telemetry"]["routes"]["counters"]["route_income_units"], 3)

        # After the third route, rotation wraps to the first sorted route.
        ordered = sorted(
            routes,
            key=lambda route: (tuple(route["edge_path"]), route["trade_resource"]),
        )
        suppressed = ordered[0]
        affected_hex = next(
            iter(game.map.edges[suppressed["edge_path"][0]].adjacent_hexes)
        )
        game.global_turn = 13
        mod.on_after_raider_moved(game, 0, affected_hex, [])

        # A move during turn 13 suppresses the route at the next income tick.
        game.global_turn = 14
        before = dict(player.resources)
        mod.on_turn_started(game, 0)
        suppressed_resource = Resource.__members__[suppressed["trade_resource"].upper()]
        self.assertEqual(
            player.resources[suppressed_resource],
            before[suppressed_resource],
        )
        self.assertEqual(sum(player.resources.values()) - sum(before.values()), 1)

    def test_active_route_is_suspended_when_an_opponent_building_cuts_its_path(self):
        mod = TradeRoutesMod()
        game = HexGame("ROUTEBLOCK", seed=21, plugins=[mod], fog_of_war=False)
        game._add_player_internal("One", False)
        game._add_player_internal("Two", False)
        game.start_game()

        route = self._register_test_routes(
            game, 0, ["grain"], path_length=4
        )[0]
        game.players[0].ext["active_routes"] = [route]
        game.global_turn = 20

        # A valid path grants both the resource income and its 3:1 bank rate.
        self.assertEqual(mod.modify_bank_rate(game, 4, 0, Resource.GRAIN), 3)
        mod.on_turn_started(game, 0)
        self.assertEqual(game.players[0].resources[Resource.GRAIN], 1)

        # The middle vertex is two roads away from both owner buildings, so a
        # rival structure can legally interrupt this four-road connection.
        blocker_vertex = route["vertices"][2]
        game.map.vertices[blocker_vertex].structure_owner = 1
        game.map.vertices[blocker_vertex].structure_type = StructureType.OUTPOST
        game.state_version += 1
        game._cache.clear()

        game.global_turn = 21
        before = game.players[0].resources[Resource.GRAIN]
        mod.on_turn_started(game, 0)
        self.assertEqual(game.players[0].resources[Resource.GRAIN], before)
        self.assertEqual(mod.modify_bank_rate(game, 4, 0, Resource.GRAIN), 4)

    def test_suppression_rounds_cover_full_table_rounds_including_movers_next_turn(self):
        mod = TradeRoutesMod(suppression_rounds=2)
        game = HexGame(
            "ROUTESUPPRESSION",
            seed=31,
            plugins=[mod],
            fog_of_war=False,
        )
        for name in ("One", "Two", "Three", "Four"):
            game._add_player_internal(name, False)
        game.start_game()

        route = self._register_test_routes(game, 0, ["grain"], path_length=2)[0]
        game.players[0].ext["active_routes"] = [route]
        affected_hex = next(
            iter(game.map.edges[route["edge_path"][0]].adjacent_hexes)
        )
        game.global_turn = 10
        mod.on_after_raider_moved(game, 0, affected_hex, [])

        # Two full table rounds on four seats: the next eight owner-turn starts
        # are suppressed. The exclusive expiry turn is global_turn + 9.
        self.assertEqual(route["suppressed_until"], 19)
        game.players[0].resources[Resource.GRAIN] = 0
        for owner_turn in (14, 18):
            game.global_turn = owner_turn
            mod.on_turn_started(game, 0)
            self.assertEqual(game.players[0].resources[Resource.GRAIN], 0)
        game.global_turn = 22
        mod.on_turn_started(game, 0)
        self.assertEqual(game.players[0].resources[Resource.GRAIN], 1)

    def test_per_route_income_mode_allows_second_route_for_same_commodity(self):
        def build_case(one_income_per_turn):
            mod = TradeRoutesMod(one_income_per_turn=one_income_per_turn)
            game = HexGame(
                f"ROUTE-SAME-RESOURCE-{one_income_per_turn}",
                seed=41,
                plugins=[mod],
                fog_of_war=False,
            )
            game._add_player_internal("One", False)
            game._add_player_internal("Two", False)
            game.start_game()
            player = game.players[0]
            player.resources[Resource.WOOL] = 2
            player.resources[Resource.GRAIN] = 2
            player.trade_pact_turn = game.global_turn  # base bank rate is already 2:1
            routes = self._register_test_routes(
                game, 0, ["grain", "grain"], path_length=2
            )
            player.ext["active_routes"] = [routes[0]]
            candidate = routes[1]
            action = Action.make(
                "activate_route",
                start_vid=candidate["start_vid"],
                end_vid=candidate["end_vid"],
                edge_path=candidate["edge_path"],
                trade_resource="grain",
            )
            context = {
                "goal": SimpleNamespace(needed={}),
                "resource_weight": lambda resource, goal: 0.1,
                "genes": {},
            }
            return mod, game, action, context

        per_route_mod, per_route_game, action, context = build_case(False)
        per_route_score = per_route_mod.score_ai_action(
            per_route_game, 0, action, context
        )
        self.assertGreater(
            per_route_score,
            -1e6,
            "per-route income makes a second same-resource route productive",
        )

        capped_mod, capped_game, capped_action, capped_context = build_case(True)
        capped_score = capped_mod.score_ai_action(
            capped_game, 0, capped_action, capped_context
        )
        self.assertEqual(
            capped_score,
            -1e6,
            "shared one-income-per-turn mode should not pay for a duplicate live commodity route",
        )

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
