import unittest
from copy import deepcopy
from unittest.mock import patch

from isles_core import Action, GamePhase, HexGame, Resource, StructureType
from mods_pack import BlightMod, _productive_tile


class BlightModeTests(unittest.TestCase):
    def _game(self, mode="tactical", points_to_win=20):
        mod = BlightMod(mode=mode, spread_chance=0.0)
        game = HexGame(
            "BLIGHTTEST", points_to_win=points_to_win, seed=7319,
            fog_of_war=False, plugins=[mod],
        )
        self.assertTrue(game._add_player_internal("Test", False).success)
        self.assertTrue(game._add_player_internal("Other", False).success)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        game.global_turn = 1
        player = game.players[0]
        for resource in (Resource.WOOD, Resource.CLAY, Resource.GRAIN, Resource.WOOL, Resource.ORE):
            player.resources[resource] = 20
        hid = next(hid for hid in sorted(game.map.hexes) if _productive_tile(game, hid))
        tile = game.map.hexes[hid]
        vertex = game.map.vertices[tile.vertex_ids[0]]
        vertex.structure_type = StructureType.OUTPOST
        vertex.structure_owner = 0
        tile.ext["blight_stage"] = 1
        return game, mod, hid

    def test_new_modes_start_with_two_hotspots_visible_through_fog(self):
        for mode in ("environmental", "factions"):
            mod = BlightMod(mode=mode, spread_chance=0.0)
            game = HexGame("BLIGHTSTART", seed=991, points_to_win=20, fog_of_war=True, plugins=[mod])
            game._add_player_internal("Test", False)
            game._add_player_internal("Other", False)
            self.assertTrue(game.start_game().success)
            infected = [hid for hid, tile in game.map.hexes.items() if int(tile.ext.get("blight_stage", 0)) > 0]
            self.assertEqual(len(infected), 2)
            observation = game.get_state_for_player(0)
            views = {int(item["hex_id"]): item for item in observation["hexes"]}
            for hid in infected:
                self.assertTrue(views[hid].get("blight_detected"), f"missing fog signal for hex {hid} in {mode}")
                if not views[hid].get("revealed"):
                    self.assertIn(views[hid].get("terrain"), (None, "unknown", "fog"))

    def test_mode_validation_and_legacy_default(self):
        self.assertEqual(BlightMod().mode, "tactical")
        self.assertEqual(BlightMod(mode="nature").mode, "environmental")
        with self.assertRaises(ValueError):
            BlightMod(mode="unknown")

    def test_environmental_mode_disables_manual_corruption(self):
        game, mod, hid = self._game("environmental")
        actions = mod.enumerate_actions(game, 0)
        self.assertTrue(any(action.type == "purify_hex" for action in actions))
        self.assertFalse(any(action.type == "corrupt_hex" for action in actions))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=hid))
        self.assertFalse(result.success)
        self.assertEqual(game.map.hexes[hid].ext["blight_stage"], 1)

    def test_tactical_mode_keeps_both_actions(self):
        game, mod, hid = self._game("tactical")
        action_types = {action.type for action in mod.enumerate_actions(game, 0)}
        self.assertIn("purify_hex", action_types)
        self.assertIn("corrupt_hex", action_types)
        self.assertEqual(game.ext["blight"]["mode"], "tactical")

    def test_new_modes_spread_from_local_frontier(self):
        game, mod, _ = self._game("environmental")
        candidates = mod._spread_candidates(game)
        self.assertTrue(candidates)
        candidate_ids = {hid for hid, _weight in candidates}
        source_ids = {
            hid for hid, tile in game.map.hexes.items()
            if tile.terrain.value in ("wasteland", "ruins")
            or int(tile.ext.get("blight_stage", 0)) > 0
        }
        for hid in candidate_ids:
            self.assertTrue(
                any(
                    hid in edge.adjacent_hexes and bool(source_ids.intersection(edge.adjacent_hexes))
                    for edge in game.map.edges.values()
                ),
                f"hex {hid} is not adjacent to a blight source",
            )

    def test_faction_alignment_rewards_order_and_caps_bonus_vp(self):
        game, mod, hid = self._game("factions")
        data = game.ext["blight"]["players"]["0"]
        for _ in range(4):
            game.map.hexes[hid].ext["blight_stage"] = 1
            game.players[0].resources[Resource.GRAIN] += 1
            result = mod.handle_purify_hex(game, 0, Action.make("purify_hex", hex_id=hid))
            self.assertTrue(result.success, result.error)
        self.assertEqual(data["alignment"], "order")
        self.assertEqual(data["purified_count"], 4)
        self.assertEqual(data["purify_vp"], 1)
        self.assertEqual(data["alternate_vp"], 1)
        self.assertEqual(game.get_victory_points(0), 2)  # outpost + faction VP

        # The Order track has its own cap; later progress must not mint more VP.
        for _ in range(8):
            game.map.hexes[hid].ext["blight_stage"] = 1
            game.players[0].resources[Resource.GRAIN] += 1
            result = mod.handle_purify_hex(game, 0, Action.make("purify_hex", hex_id=hid))
            self.assertTrue(result.success, result.error)
        self.assertEqual(data["alternate_vp"], 3)
        self.assertEqual(data["purify_vp"], 3)

    def test_cult_tears_reward_attacker_not_victim_and_can_win_after_production(self):
        # Player 0 caused the blight; player 1 owns the affected production.
        # One normal VP plus one faction VP should finish the game at the
        # production hook without being overwritten by the main-phase transition.
        game, mod, hid = self._game("factions", points_to_win=2)
        tile = game.map.hexes[hid]
        victim_vertex = game.map.vertices[tile.vertex_ids[0]]
        victim_vertex.structure_owner = 1
        victim_vertex.structure_type = StructureType.OUTPOST
        other_vertex = next(
            vertex for vid, vertex in game.map.vertices.items()
            if vid not in tile.vertex_ids and vertex.structure_owner is None
        )
        other_vertex.structure_owner = 0
        other_vertex.structure_type = StructureType.OUTPOST
        tile.ext["blight_last_corrupt_by"] = 0
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)

        # roll_dice is a mutator: cached VP reads are bypassed until its final
        # state-version/cache reset. Reproduce that surrounding mutation here.
        game._mutation_depth = 1
        try:
            for _ in range(4):
                tile.ext["blight_stage"] = 1
                gains = {1: {tile.resource: 1}}
                modified = mod.modify_resource_gains(game, gains, tile.number_token)
                self.assertNotIn(1, modified)
            attacker = game.ext["blight"]["players"]["0"]
            victim = game.ext["blight"]["players"]["1"]
            self.assertEqual(attacker["tears"], 4)
            self.assertEqual(attacker["tears_vp"], 1)
            self.assertEqual(attacker["alternate_vp"], 1)
            self.assertEqual(victim["tears"], 0)
            mod.on_after_resource_distribution(game, 0, tile.number_token)
        finally:
            game._mutation_depth = 0
            game.state_version += 1
            game._cache.clear()
        self.assertEqual(game.get_victory_points(0), 2)
        self.assertEqual(game.phase, GamePhase.FINISHED)

    def test_natural_stage_two_does_not_credit_manual_attacker_for_both_stages(self):
        game, mod, hid = self._game("factions", points_to_win=20)
        tile = game.map.hexes[hid]
        victim_vertex = game.map.vertices[tile.vertex_ids[0]]
        victim_vertex.structure_owner = 1
        victim_vertex.structure_type = StructureType.METROPOLIS

        # Player 0 paid to add stage I. The natural spread then adds stage II.
        tile.ext["blight_stage"] = 1
        tile.ext["blight_last_corrupt_by"] = 0
        tile.ext["blight_stage_sources"] = [0]
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        mod.spread_chance = 1.0
        with patch.object(mod, "_spread_candidates", return_value=[(hid, 1.0)]):
            mod.on_after_dice_rolled(game, 1, 3, 4, 7)

        self.assertEqual(tile.ext["blight_stage"], 2)
        self.assertEqual(tile.ext["blight_stage_sources"], [0, None])
        modified = mod.modify_resource_gains(
            game,
            {1: {tile.resource: 2}},
            tile.number_token,
        )
        self.assertNotIn(1, modified)
        self.assertEqual(game.ext["blight"]["players"]["0"]["tears"], 1)
        self.assertEqual(game.ext["blight"]["players"]["1"]["tears"], 0)
        self.assertEqual(game.ext["blight"]["players"]["0"]["tears"], 1)

    def test_manual_corruption_of_own_building_is_rejected_in_rules_and_actions(self):
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 0

        actions = mod.enumerate_actions(game, 0)
        self.assertFalse(any(
            action.type == "corrupt_hex" and action.p.get("hex_id") == hid
            for action in actions
        ))
        telemetry_before = deepcopy(game.ext.get("mod_telemetry"))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=hid))
        self.assertFalse(result.success)
        self.assertIn("своей постройкой", result.error.lower())
        self.assertEqual(tile.ext["blight_stage"], 0)
        # Rejected actions must not mutate telemetry: it is serialized game state.
        self.assertEqual(game.ext.get("mod_telemetry"), telemetry_before)

    def test_corruption_targets_visible_hexes_outside_own_infrastructure(self):
        game, mod, own_hex = self._game("factions")
        accessible = mod._accessible_hexes(game, 0)
        target = next(
            hid for hid, tile in sorted(game.map.hexes.items())
            if hid != own_hex and hid not in accessible and _productive_tile(game, hid)
            and not any(game.map.vertices[vid].structure_owner == 0 for vid in tile.vertex_ids)
        )
        game.map.hexes[own_hex].ext["blight_stage"] = 0
        game.map.hexes[target].ext["blight_stage"] = 0

        actions = mod.enumerate_actions(game, 0)
        self.assertTrue(any(
            action.type == "corrupt_hex" and action.p.get("hex_id") == target
            for action in actions
        ))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=target))
        self.assertTrue(result.success, result.error)
        self.assertEqual(game.map.hexes[target].ext["blight_stage"], 1)
        counters = game.ext["mod_telemetry"]["blight"]["counters"]
        self.assertEqual(counters["corrupt_targets_unoccupied"], 1)


    def test_invalid_corruption_does_not_mutate_telemetry(self):
        game, mod, own_hex = self._game("factions")
        own_tile = game.map.hexes[own_hex]
        own_tile.ext["blight_stage"] = 0
        before = deepcopy(game.ext.get("mod_telemetry"))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=own_hex))
        self.assertFalse(result.success)
        self.assertEqual(game.ext.get("mod_telemetry"), before)

        target = next(
            hid for hid, tile in sorted(game.map.hexes.items())
            if hid != own_hex and _productive_tile(game, hid)
            and not any(game.map.vertices[vid].structure_owner is not None for vid in tile.vertex_ids)
        )
        target_tile = game.map.hexes[target]
        target_tile.ext["blight_stage"] = 0
        game.players[0].resources[Resource.WOOL] = 0
        before = deepcopy(game.ext.get("mod_telemetry"))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=target))
        self.assertFalse(result.success)
        self.assertIn("шерсть", result.error.lower())
        self.assertEqual(game.ext.get("mod_telemetry"), before)
        self.assertEqual(target_tile.ext["blight_stage"], 0)

        # Max-stage and shield rejection must be just as side-effect free.
        game.players[0].resources[Resource.WOOL] = 20
        target_tile.ext["blight_stage"] = mod.max_stage
        before = deepcopy(game.ext.get("mod_telemetry"))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=target))
        self.assertFalse(result.success)
        self.assertEqual(game.ext.get("mod_telemetry"), before)

        target_tile.ext["blight_stage"] = 0
        target_tile.ext["shield_until"] = game.global_turn + 1
        before = deepcopy(game.ext.get("mod_telemetry"))
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=target))
        self.assertFalse(result.success)
        self.assertEqual(game.ext.get("mod_telemetry"), before)

    def test_corruption_telemetry_tracks_actor_target_and_target_alignment(self):
        game, mod, own_hex = self._game("factions")
        target = next(
            hid for hid, tile in sorted(game.map.hexes.items())
            if hid != own_hex and _productive_tile(game, hid)
            and not any(game.map.vertices[vid].structure_owner is not None for vid in tile.vertex_ids)
        )
        tile = game.map.hexes[target]
        tile.ext["blight_stage"] = 0
        target_vertex = game.map.vertices[tile.vertex_ids[0]]
        target_vertex.structure_type = StructureType.OUTPOST
        target_vertex.structure_owner = 1
        mod._change_alignment(game, 1, +1)
        mod._change_alignment(game, 1, +1)
        self.assertEqual(mod._player_blight_state(game, 1)["alignment"], "cult")

        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=target))
        self.assertTrue(result.success, result.error)
        telemetry = game.ext["mod_telemetry"]["blight"]
        self.assertEqual(telemetry["counters"]["corrupt_targets_cult_aligned"], 1)
        pair = telemetry["corruption_relationships"]["0->1"]
        self.assertEqual(pair["actor_id"], 0)
        self.assertEqual(pair["target_owner_id"], 1)
        self.assertEqual(pair["actions"], 1)
        self.assertEqual(pair["target_alignment"], {"cult": 1})
        event = next(e for e in reversed(telemetry["events"]) if e["kind"] == "corrupt")
        self.assertEqual(event["player_id"], 0)
        self.assertEqual(event["target_owner_ids"], [1])
        self.assertEqual(event["target_alignments"], {"1": "cult"})

    def test_fog_limits_corruption_to_visible_hexes_not_infrastructure(self):
        game, mod, own_hex = self._game("factions")
        accessible = mod._accessible_hexes(game, 0)
        visible_target = next(
            hid for hid, tile in sorted(game.map.hexes.items())
            if hid != own_hex and hid not in accessible and _productive_tile(game, hid)
            and not any(game.map.vertices[vid].structure_owner == 0 for vid in tile.vertex_ids)
        )
        hidden_target = next(
            hid for hid, tile in sorted(game.map.hexes.items())
            if hid not in (own_hex, visible_target) and _productive_tile(game, hid)
        )
        game.fog_of_war = True
        game.players[0].revealed_hexes = {visible_target}
        mod._access_cache.pop(game, None)
        game.map.hexes[visible_target].ext["blight_stage"] = 0
        game.map.hexes[hidden_target].ext["blight_stage"] = 0

        actions = mod.enumerate_actions(game, 0)
        corrupt_ids = {
            action.p.get("hex_id") for action in actions if action.type == "corrupt_hex"
        }
        self.assertIn(visible_target, corrupt_ids)
        self.assertNotIn(hidden_target, corrupt_ids)
        self.assertFalse(mod.handle_corrupt_hex(
            game, 0, Action.make("corrupt_hex", hex_id=hidden_target)
        ).success)

    def test_hidden_accusation_ai_scoring_uses_stage_sources_safely(self):
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("BLIGHTACCSCORE", seed=1237, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Order", False)
        game._add_player_internal("Potential Cult", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        order = mod._player_blight_state(game, 0)
        order["alignment"] = "order"
        order["alignment_score"] = -2
        game.players[0].resources[Resource.WOOD] = 1
        target = mod._player_blight_state(game, 1)
        target["alignment"] = "cult"
        target["alignment_score"] = 2
        tile = next(tile for tile in game.map.hexes.values() if _productive_tile(game, tile.hex_id))
        tile.ext["blight_stage"] = 1
        tile.ext["blight_stage_sources"] = [1]
        actions = mod.enumerate_actions(game, 0)
        accusation = next(
            action for action in actions
            if action.type == "accuse_cultist" and action.p.get("target_player_id") == 1
        )
        score = mod.score_ai_action(game, 0, accusation, {})
        self.assertGreater(score, 0.0)

    def test_natural_spread_does_not_award_tears_to_the_affected_owner(self):
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        self.assertNotIn("blight_last_corrupt_by", tile.ext)
        # The Cult player is not the affected owner; another player's building
        # receives the natural-spread penalty, but no attacker gets Tears.
        vertex = game.map.vertices[tile.vertex_ids[0]]
        vertex.structure_owner = 1
        vertex.structure_type = StructureType.OUTPOST
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        modified = mod.modify_resource_gains(
            game, {1: {tile.resource: 1}}, tile.number_token
        )
        self.assertNotIn(1, modified)
        self.assertEqual(game.ext["blight"]["players"]["0"]["tears"], 0)

    def test_faction_alignment_requires_repeated_actions(self):
        game, mod, _ = self._game("factions")
        self.assertEqual(mod._change_alignment(game, 0, +1), "neutral")
        self.assertEqual(mod._change_alignment(game, 0, +1), "cult")
        self.assertEqual(mod._change_alignment(game, 0, -1), "cult")
        self.assertEqual(mod._change_alignment(game, 0, -1), "neutral")


    def test_hidden_faction_observation_hides_other_players_until_revealed(self):
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("BLIGHTHIDE", seed=1234, points_to_win=20, fog_of_war=True, plugins=[mod])
        game._add_player_internal("Order", False)
        game._add_player_internal("Cult", False)
        self.assertTrue(game.start_game().success)
        cult = mod._player_blight_state(game, 1)
        cult["alignment"] = "cult"
        cult["alignment_score"] = 2
        cult["tears"] = 3
        observation = game.get_state_for_player(0)
        other = next(player for player in observation["players"] if player["player_id"] == 1)
        self.assertEqual(other["blight"]["alignment"], "hidden")
        self.assertIsNone(other["blight"]["tears"])
        self.assertIsNone(other["blight"]["purified_count"])
        self.assertIsNone(other["blight"]["bonus_vp"])
        self.assertEqual(observation["me"]["blight"]["alignment"], "neutral")
        cult["cult_revealed"] = True
        observation = game.get_state_for_player(0)
        other = next(player for player in observation["players"] if player["player_id"] == 1)
        self.assertEqual(other["blight"]["alignment"], "cult")
        self.assertEqual(other["blight"]["tears"], 3)

    def test_hidden_cult_correct_accusation_reveals_and_rewards_order(self):
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("BLIGHTACC", seed=1235, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Order", False)
        game._add_player_internal("Cult", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        order = mod._player_blight_state(game, 0)
        cult = mod._player_blight_state(game, 1)
        order["alignment"] = "order"
        order["alignment_score"] = -2
        cult["alignment"] = "cult"
        cult["alignment_score"] = 2
        cult["tears"] = 4
        from isles_core import Action, Resource
        game.players[0].resources[Resource.WOOD] = 1
        result = game.apply_action(0, Action.make("accuse_cultist", target_player_id=1))
        self.assertTrue(result.success, result.error)
        self.assertTrue(cult["cult_revealed"])
        self.assertEqual(cult["tears"], 2)
        self.assertEqual(order["accusation_vp"], 1)
        self.assertEqual(game.get_victory_points(0), 1)
        self.assertFalse(any(a.type == "accuse_cultist" for a in mod.enumerate_actions(game, 0)))

    def test_hidden_cult_wrong_accusation_costs_one_resource(self):
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("BLIGHTWRONG", seed=1236, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Order", False)
        game._add_player_internal("Neutral", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        order = mod._player_blight_state(game, 0)
        order["alignment"] = "order"
        order["alignment_score"] = -2
        game.players[0].resources[next(iter(Resource))] = 1
        before = sum(game.players[0].resources.values())
        engine_rng_before = game.rng.getstate()
        from isles_core import Action
        result = game.apply_action(0, Action.make("accuse_cultist", target_player_id=1))
        self.assertTrue(result.success, result.error)
        self.assertEqual(sum(game.players[0].resources.values()), before - 1)
        self.assertTrue(order["accusation_used"])
        self.assertEqual(game.rng.getstate(), engine_rng_before)
        self.assertIn("blight:accusation", game.ext["plugin_rng_states"])


    def test_cult_immunity_and_own_infected_hex_tear_income(self):
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        state = mod._player_blight_state(game, 0)
        state["alignment"] = "cult"
        state["alignment_score"] = 2
        tile.ext["blight_stage"] = 2
        tile.ext["blight_stage_sources"] = [0, None]
        result = mod.modify_resource_gains(game, {0: {tile.resource: 1}}, tile.number_token)
        self.assertEqual(result[0].get(tile.resource), 1)  # no production suppression
        self.assertEqual(state["tears"], 1)  # an outpost earns one tear per roll
        self.assertEqual(game.ext["mod_telemetry"]["blight"]["counters"]["cult_immunity_tears"], 1)

    def test_order_cleanse_grants_three_round_production_blessing(self):
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        state = mod._player_blight_state(game, 0)
        state["alignment"] = "order"
        state["alignment_score"] = -2
        tile.ext["blight_stage"] = 1
        game.players[0].resources[Resource.GRAIN] = 20
        result = mod.handle_purify_hex(game, 0, Action.make("purify_hex", hex_id=hid))
        self.assertTrue(result.success, result.error)
        blessings = tile.ext.get("blight_order_blessings", [])
        self.assertTrue(any(item["player_id"] == 0 and item["until_turn"] >= game.global_turn + 5 for item in blessings))
        gains = mod.modify_resource_gains(game, {0: {tile.resource: 1}}, tile.number_token)
        self.assertGreaterEqual(gains[0].get(tile.resource, 0), 2)

    def test_shipped_balance_defaults_pin_the_measured_configuration(self):
        # The paired 5000-game A/B measured these exact defaults; a silent flip
        # invalidates that measurement, so pin them explicitly.
        mod = BlightMod(mode="factions")
        self.assertFalse(mod.cult_suppression_theft)
        self.assertFalse(mod.tears_prealignment)
        self.assertTrue(mod.blessing_per_building)
        self.assertFalse(mod.cult_corrupt_tear)
        self.assertEqual(mod.tears_vp_step, 3)
        self.assertEqual(mod.purify_vp_step, 3)
        self.assertEqual(mod.ai_corrupt_aggression, 1.0)

    def test_cult_suppression_theft_feeds_the_corruptor(self):
        # A Cult-sourced suppression hands the lost units to the corruptor;
        # a neutral corruptor only banks tears for a future Cult claim.
        game, mod, hid = self._game("factions", points_to_win=20)
        mod.cult_suppression_theft = True  # optional house rule, off by default
        tile = game.map.hexes[hid]
        victim_vertex = game.map.vertices[tile.vertex_ids[0]]
        victim_vertex.structure_owner = 1
        victim_vertex.structure_type = StructureType.OUTPOST
        tile.ext["blight_stage"] = 1
        tile.ext["blight_stage_sources"] = [0]
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        modified = mod.modify_resource_gains(game, {1: {tile.resource: 1}}, tile.number_token)
        self.assertNotIn(1, modified)
        self.assertEqual(modified.get(0, {}).get(tile.resource), 1)
        self.assertEqual(game.ext["mod_telemetry"]["blight"]["counters"]["cult_stolen_units"], 1)
        self.assertEqual(game.ext["blight"]["players"]["0"]["tears"], 1)
        # The theft is announced in the log at the moment it happens.
        self.assertTrue(any("высосал" in entry["msg"] and "1×" in entry["msg"] for entry in game.log))

        # Same corruption sourced by a still-neutral player: with the shipped
        # gating a neutral corruptor banks neither tears nor stolen units.
        tile.ext["blight_stage_sources"] = [0]
        game.ext["blight"]["players"]["0"]["alignment"] = "neutral"
        game.ext["blight"]["players"]["0"]["alignment_score"] = 1
        modified = mod.modify_resource_gains(game, {1: {tile.resource: 1}}, tile.number_token)
        self.assertNotIn(1, modified)
        self.assertNotIn(0, modified)
        self.assertEqual(game.ext["blight"]["players"]["0"]["tears"], 1)

    def test_multi_resource_theft_sorts_and_pays_both_types_in_one_roll(self):
        # Resource is a plain Enum; logging must not rely on ordering when one
        # thief drains two different resource types from one victim at once.
        game, mod, hid = self._game("factions", points_to_win=20)
        mod.cult_suppression_theft = True
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        first = game.map.hexes[hid]
        # A second productive hex that produces on the SAME number with a
        # different resource: one distribution covers both.
        second = next(
            game.map.hexes[h]
            for h in sorted(game.map.hexes)
            if h != hid and _productive_tile(game, h)
            and game.map.hexes[h].resource != first.resource
            and game.map.hexes[h].number_token == first.number_token
        )
        rolled = first.number_token
        for tile in (first, second):
            victim = game.map.vertices[tile.vertex_ids[0]]
            victim.structure_owner = 1
            victim.structure_type = StructureType.OUTPOST
            tile.ext["blight_stage"] = 1
            tile.ext["blight_stage_sources"] = [0]
        gains = {1: {tile.resource: 1 for tile in (first, second)}}
        # Two different Resource keys in one distribution: sorting them without
        # an explicit key would raise TypeError on plain Enum comparison.
        try:
            modified = mod.modify_resource_gains(game, gains, rolled)
        except TypeError:
            self.fail("theft log sorting compared Resource members")
        stolen = modified.get(0, {})
        self.assertEqual(len(stolen), 2)
        for tile in (first, second):
            self.assertEqual(stolen.get(tile.resource), 1)
        self.assertNotIn(1, modified)
        self.assertTrue(any("высосал" in entry["msg"] for entry in game.log))

    def test_cult_corrupt_tear_milestone_mints_vp_for_established_cult(self):
        # With the house rule on, an already-Cult corruptor must claim the VP
        # step its bonus tear crosses — not only the player who just joined.
        game, mod, hid = self._game("factions", points_to_win=20)
        mod.cult_corrupt_tear = True
        state = mod._player_blight_state(game, 0)
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        state["tears"] = 5  # one more tear crosses the step-3 milestone twice
        game.map.hexes[hid].ext["blight_stage"] = 0
        target = next(
            h for h in sorted(game.map.hexes)
            if h != hid and _productive_tile(game, h)
            and not any(game.map.vertices[vid].structure_owner == 0 for vid in game.map.hexes[h].vertex_ids)
        )
        game.players[0].resources[Resource.WOOL] = 1
        result = mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=target))
        self.assertTrue(result.success, result.error)
        self.assertEqual(state["tears"], 6)
        self.assertGreaterEqual(state["tears_vp"], 1)
        self.assertGreaterEqual(state["alternate_vp"], 1)

    def test_corrupt_scoring_skips_cult_immune_victims_in_open_mode(self):
        # In open mode the AI must not count suppression damage against a
        # Cult-aligned owner (they are immune and even earn Tears), and must
        # price the strike as feeding the opponent.
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 0
        victim = game.map.vertices[tile.vertex_ids[0]]
        victim.structure_owner = 1
        victim.structure_type = StructureType.OUTPOST
        game.players[0].resources[Resource.WOOL] = 2
        action = Action.make("corrupt_hex", hex_id=hid)
        context = {"my_vp": 0, "leader_vp": 0, "leader_id": 2}

        neutral_score = mod.score_ai_action(game, 0, action, dict(context))
        cult = mod._player_blight_state(game, 1)
        cult["alignment"] = "cult"
        cult["alignment_score"] = 2
        cult_score = mod.score_ai_action(game, 0, action, dict(context))
        self.assertIsInstance(neutral_score, float)
        self.assertGreater(neutral_score, cult_score)
        # Feeding a Cult owner is a net negative, not just a smaller positive.
        self.assertLess(cult_score, 0.0)

    def test_corrupt_scoring_in_hidden_mode_discounts_by_evidence_not_alignment(self):
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("BLIGHTHIDDENSCORE", seed=4441, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Bot", False)
        game._add_player_internal("Quiet", False)
        game._add_player_internal("Corruptor", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        player = game.players[0]
        for resource in Resource:
            player.resources[resource] = 5
        hid = next(h for h in sorted(game.map.hexes) if _productive_tile(game, h))
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 0
        vertex = game.map.vertices[tile.vertex_ids[0]]
        vertex.structure_owner = 1
        vertex.structure_type = StructureType.OUTPOST
        quiet_owner = game.map.vertices[tile.vertex_ids[0]]
        action = Action.make("corrupt_hex", hex_id=hid)
        context = {"my_vp": 0, "leader_vp": 0, "leader_id": 2}

        base = mod.score_ai_action(game, 0, action, dict(context))
        # Quiet owner is secretly Cult; hidden mode must not peek, so the
        # score may not react to the hidden alignment at all…
        secret = mod._player_blight_state(game, 1)
        secret["alignment"] = "cult"
        secret["alignment_score"] = 2
        secret_score = mod.score_ai_action(game, 0, action, dict(context))
        self.assertAlmostEqual(secret_score, base)
        # …while genuine corruption evidence attributed to the owner must
        # discount the expected damage. One stage-2 hex carries at most two
        # attributed stages: that is the intermediate, discounted step.
        evidence_tiles = [
            t for t in game.map.hexes.values()
            if _productive_tile(game, t.hex_id) and t.hex_id != hid
        ][:2]
        evidence_tiles[0].ext["blight_stage"] = 2
        evidence_tiles[0].ext["blight_stage_sources"] = [1, 1]
        # Direct ext mutation bypasses the engine wrapper, so bump the version
        # the suspicion cache is keyed on, exactly like a real corrupt would.
        game.state_version += 1
        game._cache.clear()
        suspicious_score = mod.score_ai_action(game, 0, action, dict(context))
        self.assertLess(suspicious_score, secret_score)
        # Four clues is conviction: the owner's yield is excluded entirely,
        # exactly like an open-mode Cult victim, and the strike is penalised.
        evidence_tiles[1].ext["blight_stage"] = 2
        evidence_tiles[1].ext["blight_stage_sources"] = [1, 1]
        game.state_version += 1
        game._cache.clear()
        convinced_score = mod.score_ai_action(game, 0, action, dict(context))
        self.assertLess(convinced_score, suspicious_score)

    def test_diagnostic_ablation_switches_are_coherent_and_default_off(self):
        # accusation_enabled=False must remove the action everywhere at once
        # (enumeration, handler, AI scoring) and record the rejection without
        # touching anything else; ai_hidden_suspicion=False must restore the
        # pre-heuristic blind valuation. Defaults equal the shipped rules.
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        self.assertTrue(mod.accusation_enabled)
        self.assertTrue(mod.ai_hidden_suspicion)
        game = HexGame("ABLATE", seed=4711, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("О", False)
        game._add_player_internal("К", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        order = mod._player_blight_state(game, 0)
        order["alignment"] = "order"
        order["alignment_score"] = -2
        cult = mod._player_blight_state(game, 1)
        cult["alignment"] = "cult"
        cult["alignment_score"] = 2
        game.players[0].resources[Resource.WOOD] = 1
        accusation = Action.make("accuse_cultist", target_player_id=1)
        # The accusation scorer demands visible evidence: one attributed
        # blight stage makes the suspicion grounded.
        evidence_tile = next(t for t in game.map.hexes.values() if _productive_tile(game, t.hex_id))
        evidence_tile.ext["blight_stage"] = 1
        evidence_tile.ext["blight_stage_sources"] = [1]

        self.assertTrue(any(a.type == "accuse_cultist" for a in mod.enumerate_actions(game, 0)))
        self.assertGreater(mod.score_ai_action(game, 0, accusation, {}), 0.0)

        mod.accusation_enabled = False
        self.assertFalse(any(a.type == "accuse_cultist" for a in mod.enumerate_actions(game, 0)))
        self.assertEqual(mod.score_ai_action(game, 0, accusation, {}), -1e6)
        result = mod.handle_accuse_cultist(game, 0, accusation)
        self.assertFalse(result.success)
        self.assertFalse(order["accusation_used"])

        mod.accusation_enabled = True
        applied = mod.handle_accuse_cultist(game, 0, accusation)
        self.assertTrue(applied.success, applied.error)
        # The event log records the turn so diagnostics can find the first one.
        events = game.ext["mod_telemetry"]["blight"]["events"]
        self.assertTrue(any(e.get("kind") == "accuse" and e.get("correct") for e in events))

    def test_ai_hidden_suspicion_ablation_restores_blind_valuation(self):
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("ABLATESUSP", seed=4712, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Bot", False)
        game._add_player_internal("Quiet", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        for r in Resource:
            game.players[0].resources[r] = 5
        hid = next(h for h in sorted(game.map.hexes) if _productive_tile(game, h))
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 0
        vertex = game.map.vertices[tile.vertex_ids[0]]
        vertex.structure_owner = 1
        vertex.structure_type = StructureType.OUTPOST
        action = Action.make("corrupt_hex", hex_id=hid)
        context = {"my_vp": 0, "leader_vp": 0, "leader_id": 2}
        # Four clues: conviction while the heuristic is on...
        evidence = [t for t in game.map.hexes.values()
                    if _productive_tile(game, t.hex_id) and t.hex_id != hid][:2]
        for t in evidence:
            t.ext["blight_stage"] = 2
            t.ext["blight_stage_sources"] = [1, 1]
        game.state_version += 1
        game._cache.clear()
        heuristic_on = mod.score_ai_action(game, 0, action, dict(context))
        # ...and a blind full-valuation strike once it is ablated.
        mod.ai_hidden_suspicion = False
        blind = mod.score_ai_action(game, 0, action, dict(context))
        self.assertGreater(blind, heuristic_on)

    def test_hidden_journal_stays_anonymous_and_open_keeps_names(self):
        # LEAK FIX: the game log is public (every client receives it, author id
        # included), and alignment is a deterministic sum of public corrupt/purify
        # actions. In hidden mode those journal entries must not name the actor,
        # otherwise the hidden alignment is reconstructible by arithmetic.
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("HIDDENLOG", seed=5150, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Культист", False)
        game._add_player_internal("Наблюдатель", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        for r in Resource:
            game.players[0].resources[r] = 5
        hid = next(h for h in sorted(game.map.hexes)
                   if all(game.map.vertices[v].structure_owner is None for v in game.map.hexes[h].vertex_ids))
        game.apply_action(0, Action.make("corrupt_hex", hex_id=hid))
        entry = next(e for e in game.log if "осквернил" in e["msg"])
        self.assertNotIn("Культист", entry["msg"])
        self.assertIsNone(entry["player_id"], "journal author id must be dropped in hidden mode")

        # A cleanse is likewise anonymous...
        game.map.hexes[hid].ext["blight_stage"] = 1
        game.current_player_id = 1
        game.players[1].resources[Resource.GRAIN] += 1
        game.players[1].resources[Resource.WOOL] += 1
        vertex = game.map.vertices[game.map.hexes[hid].vertex_ids[0]]
        vertex.structure_owner = 1
        vertex.structure_type = StructureType.OUTPOST
        game.apply_action(1, Action.make("purify_hex", hex_id=hid))
        clean_entry = next(e for e in reversed(game.log) if "очистил" in e["msg"])
        self.assertNotIn("Наблюдатель", clean_entry["msg"])
        self.assertIsNone(clean_entry["player_id"])

        # ...while the open mode keeps the names and the author id.
        open_mod = BlightMod(mode="factions", spread_chance=0.0)
        open_game = HexGame("OPENLOG", seed=5151, points_to_win=20, fog_of_war=False, plugins=[open_mod])
        open_game._add_player_internal("Культист", False)
        open_game._add_player_internal("Наблюдатель", False)
        self.assertTrue(open_game.start_game().success)
        open_game.phase = GamePhase.MAIN
        open_game.current_player_id = 0
        for r in Resource:
            open_game.players[0].resources[r] = 5
        open_hid = next(h for h in sorted(open_game.map.hexes)
                        if all(open_game.map.vertices[v].structure_owner is None for v in open_game.map.hexes[h].vertex_ids))
        open_game.apply_action(0, Action.make("corrupt_hex", hex_id=open_hid))
        open_entry = next(e for e in open_game.log if "осквернил" in e["msg"])
        self.assertIn("Культист", open_entry["msg"])
        self.assertEqual(open_entry["player_id"], 0)

    def test_open_alignment_aware_ablation_restores_blind_valuation(self):
        # O1 diagnostic: with open_alignment_aware=False the open-mode corrupt
        # scoring must stop skipping Cult-owned sectors and stop applying the
        # feeding penalty; the shipped default (True) keeps them.
        game, mod, hid = self._game("factions")
        mod.cult_visibility = "open"
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 0
        victim = game.map.vertices[tile.vertex_ids[0]]
        victim.structure_owner = 1
        victim.structure_type = StructureType.OUTPOST
        cult = mod._player_blight_state(game, 1)
        cult["alignment"] = "cult"
        cult["alignment_score"] = 2
        game.players[0].resources[Resource.WOOL] = 2
        action = Action.make("corrupt_hex", hex_id=hid)
        context = {"my_vp": 0, "leader_vp": 0, "leader_id": 2}

        aware_score = mod.score_ai_action(game, 0, action, dict(context))
        self.assertLess(aware_score, 0.0)  # feeding penalty makes it a bad strike
        mod.open_alignment_aware = False
        blind_score = mod.score_ai_action(game, 0, action, dict(context))
        self.assertGreater(blind_score, aware_score)
        self.assertGreater(blind_score, 0.0)  # valued like a normal suppression

    def test_attack_reconstructs_hidden_alignment_from_public_observations(self):
        # INFORMATION-AUDIT ATTACK (variant-A contract): an observer sees only
        # what a real client sees — get_state_for_player snapshots. The journal
        # is anonymized ("кто-то осквернил…"), but the game is strictly
        # turn-based and the current player is public, so every anonymous
        # corrupt/purify entry is attributable to the player on move. Because
        # alignment is a deterministic sum of those public actions, the hidden
        # alignment MUST be reconstructible. This test proves it: the shipped
        # "hidden" mode is a deduction mode (publicly computable faction), not
        # a truly secret role. See docs/blight_information_audit.md.
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("ATTACK", seed=6060, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Культист", False)
        game._add_player_internal("Орденец", False)
        game._add_player_internal("Наблюдатель", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        for r in Resource:
            game.players[0].resources[r] = 5
            game.players[1].resources[r] = 5
            game.players[2].resources[r] = 5
        targets = [h for h in sorted(game.map.hexes)
                   if all(game.map.vertices[v].structure_owner is None for v in game.map.hexes[h].vertex_ids)][:4]

        observer = 2
        seen_ids = set()
        journal = []            # accumulated public journal of the observer
        seen_current = None     # public state["current_player"], last observed

        def observe():
            nonlocal seen_current
            state = game.get_state_for_player(observer)
            for entry in state["log"]:
                if entry["id"] not in seen_ids:
                    seen_ids.add(entry["id"])
                    journal.append(entry)
            seen_current = state["current_player"]

        # Scenario (all actions in their own turn, like a real table):
        # P0 corrupts targets[0] -> end; P1 rolls, purifies targets[0] -> end;
        # P2 rolls, passes -> end; P0 rolls, corrupts targets[1].
        # P0 ends at score +2 = Cult, P1 at -1 = neutral; the observer
        # reconstructs all of it from public data.
        vertex = game.map.vertices[game.map.hexes[targets[0]].vertex_ids[0]]
        vertex.structure_owner = 1
        vertex.structure_type = StructureType.OUTPOST
        game.players[0].resources[Resource.WOOL] += 2
        game.players[1].resources[Resource.GRAIN] += 1
        observe()
        game.apply_action(0, Action.make("corrupt_hex", hex_id=targets[0]))
        observe()
        game.end_turn(game.current_player_id)
        game.apply_action(1, Action.make("roll_dice"))
        observe()
        game.apply_action(1, Action.make("purify_hex", hex_id=targets[0]))
        observe()
        game.end_turn(game.current_player_id)
        game.apply_action(2, Action.make("roll_dice"))
        observe()
        game.end_turn(game.current_player_id)
        game.apply_action(0, Action.make("roll_dice"))
        observe()
        game.apply_action(0, Action.make("corrupt_hex", hex_id=targets[1]))
        observe()

        # --- attacker logic, public data only ---
        # Two public sources identify the actor: turn markers inside the
        # journal ("🎲 X: …", "⏭️ Ход переходит к X") and the always-current
        # state["current_player"] field. Both are visible to any client.
        current_name = None
        current_id = None
        name_to_id = {p.name: p.player_id for p in game.players}
        score = {p.player_id: 0 for p in game.players}
        last_snap_turn = -1
        for entry in sorted(journal, key=lambda e: e["id"]):
            msg = entry["msg"]
            if msg.startswith("🎲 "):
                current_name = msg[2:].split(":")[0].strip()
                current_id = name_to_id.get(current_name, current_id)
            elif msg.startswith("⏭️ Ход переходит к "):
                current_name = msg[len("⏭️ Ход переходит к "):].strip()
                current_id = name_to_id.get(current_name, current_id)
            elif "осквернил" in msg or "очистил" in msg:
                actor = current_id if current_id is not None else seen_current
                self.assertIsNotNone(actor, "anonymous action with no public owner at all")
                score[actor] += 1 if "осквернил" in msg else -1

        # Reconstructed faction vs the real (mod-internal) one.
        real = {p.player_id: str(mod._player_blight_state(game, p.player_id)["alignment"]) for p in game.players}
        for p in game.players:
            s = score[p.player_id]
            reconstructed = "cult" if s >= 2 else "order" if s <= -2 else "neutral"
            self.assertEqual(reconstructed, real[p.player_id],
                             f"reconstruction mismatch for {p.name}: {s} vs {real[p.player_id]}")
        # And specifically: the hidden Cult membership is fully exposed.
        self.assertEqual(real[0], "cult")
        self.assertEqual(score[0], 2)

    def test_suspicion_evidence_is_publicly_reconstructible_in_variant_A(self):
        # PRIVILEGE AUDIT: the corruption scorer reads tile.ext
        # ["blight_stage_sources"] — an internal field clients never receive.
        # In the shipped variant-A hidden mode, however, every manual
        # corruption is attributable from public data (turn markers + journal,
        # see the attack test above), so the suspicion counters built from the
        # public journal must equal the internal ones. If that stops holding,
        # the bots gained information humans cannot reconstruct.
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("PUBLICEVID", seed=7070, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("Культист", False)
        game._add_player_internal("Наблюдатель", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        game.players[0].resources[Resource.WOOL] += 2
        targets = [h for h in sorted(game.map.hexes)
                   if all(game.map.vertices[v].structure_owner is None for v in game.map.hexes[h].vertex_ids)][:2]
        game.apply_action(0, Action.make("corrupt_hex", hex_id=targets[0]))
        game.end_turn(0)
        game.apply_action(1, Action.make("roll_dice"))
        game.apply_action(1, Action.make("end_turn"))
        game.apply_action(0, Action.make("roll_dice"))
        game.apply_action(0, Action.make("corrupt_hex", hex_id=targets[1]))

        # Public reconstruction: anonymous journal entries attributed via the
        # acting player — turn markers plus the always-public
        # state["current_player"] from the observer's snapshots — counting the
        # hexes and their CURRENT stages exactly like _cult_suspicion does.
        public_counts = {p.player_id: 0 for p in game.players}
        current_id = None
        name_to_id = {p.name: p.player_id for p in game.players}
        seen_ids = set()
        seen_current = None
        for entry in game.log:
            seen_ids.add(entry["id"])
        state = game.get_state_for_player(1)
        seen_current = state["current_player"]
        for entry in sorted(game.log, key=lambda e: e["id"]):
            msg = entry["msg"]
            if msg.startswith("🎲 "):
                current_id = name_to_id.get(msg[2:].split(":")[0].strip(), current_id)
            elif msg.startswith("⏭️ Ход переходит к "):
                current_id = name_to_id.get(msg[len("⏭️ Ход переходит к "):].strip(), current_id)
            elif "осквернил сектор" in msg:
                actor = current_id if current_id is not None else seen_current
                hid = int(msg.rsplit(" ", 1)[-1])
                public_counts[actor] += 1

        # Public attribution must reproduce the internal per-owner suspicion.
        for pid in public_counts:
            self.assertEqual(
                public_counts[pid],
                mod._cult_suspicion(game, pid),
                f"public evidence != internal blight_stage_sources for player {pid}",
            )
        # The prime suspect is the actual corruptor.
        self.assertEqual(public_counts[0], 2)
        self.assertEqual(mod._cult_suspicion(game, 0), 2)

    def test_gene_dose_isolation(self):
        # GENE DOSE PRECHECK: blight.purify_bias scales ONLY the purify score,
        # blight.corrupt_bias ONLY the corrupt score; costs, rewards and the
        # other action's valuation are untouched. Purify is scored on the
        # player's OWN infected hex, corrupt on an enemy-held clean hex (the
        # legality mirror of each action).
        game, mod, hid = self._game("factions")
        own_tile = game.map.hexes[hid]
        own_tile.ext["blight_stage"] = 1
        # Enemy hex for corruption: no own building there, one enemy outpost.
        enemy_hex = next(h for h in sorted(game.map.hexes)
                         if h != hid and _productive_tile(game, h)
                         and all(game.map.vertices[v].structure_owner is None
                                 for v in game.map.hexes[h].vertex_ids))
        enemy_tile = game.map.hexes[enemy_hex]
        enemy_vertex = game.map.vertices[enemy_tile.vertex_ids[0]]
        enemy_vertex.structure_owner = 1
        enemy_vertex.structure_type = StructureType.OUTPOST
        game.players[0].resources[Resource.WOOL] = 3
        game.players[0].resources[Resource.GRAIN] = 3
        purify_action = Action.make("purify_hex", hex_id=hid)
        corrupt_action = Action.make("corrupt_hex", hex_id=enemy_hex)
        ctx = {"my_vp": 0, "leader_vp": 0, "leader_id": 2}

        def scores(genes):
            return (mod.score_ai_action(game, 0, purify_action, {**ctx, "genes": genes}),
                    mod.score_ai_action(game, 0, corrupt_action, {**ctx, "genes": genes}))

        base_p, base_c = scores({"blight.purify_bias": 1.0, "blight.corrupt_bias": 1.0})
        strong_p, c_when_p = scores({"blight.purify_bias": 2.0, "blight.corrupt_bias": 1.0})
        p_when_c, strong_c = scores({"blight.purify_bias": 1.0, "blight.corrupt_bias": 2.0})
        self.assertGreater(strong_p, base_p)
        self.assertAlmostEqual(c_when_p, base_c)   # purify dose must not touch corrupt score
        self.assertGreater(strong_c, base_c)
        self.assertAlmostEqual(p_when_c, base_p)   # corrupt dose must not touch purify score

    def test_suppression_split_by_victim_alignment(self):
        # Telemetry split: who bears the economic damage, by the victim's
        # alignment at the moment of suppression (internal counters only).
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        victim = game.map.vertices[tile.vertex_ids[0]]
        victim.structure_owner = 1
        victim.structure_type = StructureType.OUTPOST
        tile.ext["blight_stage"] = 1
        tile.ext["blight_stage_sources"] = [0]
        # Neutral victim.
        counters = game.ext["mod_telemetry"]["blight"]["counters"]
        mod.modify_resource_gains(game, {1: {tile.resource: 1}}, tile.number_token)
        self.assertEqual(counters.get("suppressed_on_neutral"), 1)
        # Order victim.
        order_state = mod._player_blight_state(game, 1)
        order_state["alignment"] = "order"
        order_state["alignment_score"] = -2
        tile.ext["blight_stage"] = 1
        mod.modify_resource_gains(game, {1: {tile.resource: 1}}, tile.number_token)
        self.assertEqual(counters.get("suppressed_on_order"), 1)
        # Cult victim: immune, nothing suppressed, no split entry.
        order_state["alignment"] = "cult"
        order_state["alignment_score"] = 2
        tile.ext["blight_stage"] = 1
        mod.modify_resource_gains(game, {1: {tile.resource: 1}}, tile.number_token)
        self.assertIsNone(counters.get("suppressed_on_cult"))
        self.assertEqual(counters["resource_units_suppressed"], 2)

    def test_diagnostic_head_start_grants_role_without_retroactive_vp(self):
        # The head-start experiment must be able to start a bot as Cult/Order
        # WITHOUT retroactive faction VP: no tear/purify steps, no bonus VP,
        # and the role's effects (immunity) apply immediately.
        game, mod, hid = self._game("factions")
        state = mod._player_blight_state(game, 0)
        state["alignment_score"] = 2
        state["alignment"] = "cult"
        vp_before = game.get_victory_points(0)
        self.assertEqual(state["tears_vp"], 0)
        self.assertEqual(state["alternate_vp"], 0)
        self.assertEqual(vp_before, 1)  # just the outpost, no faction VP minted
        # Immunity applies immediately: own infected hex suppresses nothing.
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 2
        tile.ext["blight_stage_sources"] = [None, None]
        modified = mod.modify_resource_gains(game, {0: {tile.resource: 1}}, tile.number_token)
        self.assertEqual(modified[0].get(tile.resource), 1)
        # And a later natural flip is still required to mint faction VP: an
        # Order starter earns purify VP only after its own cleanses.
        other = mod._player_blight_state(game, 1)
        other["alignment_score"] = -2
        other["alignment"] = "order"
        self.assertEqual(other["purify_vp"], 0)
        self.assertEqual(game.get_victory_points(1), 0)  # no structures, no retro VP

    def _shared_cap_game(self):
        # Player 0 keeps its outpost (first vertex); the victim takes another
        # vertex of the same hex so purify stays accessible to p0.
        game, mod, hid = self._game("factions")
        tile = game.map.hexes[hid]
        own_vid = next(vid for vid in tile.vertex_ids
                       if game.map.vertices[vid].structure_owner == 0)
        victim = game.map.vertices[next(vid for vid in tile.vertex_ids if vid != own_vid)]
        victim.structure_owner = 1
        victim.structure_type = StructureType.OUTPOST
        return game, mod, hid, tile

    def _grant_tears(self, mod, game, hid, tile, amount):
        """Attribute `amount` tears to player 0 via one production roll."""
        tile.ext["blight_stage"] = 1
        tile.ext["blight_stage_sources"] = [0]
        mod.modify_resource_gains(game, {1: {tile.resource: amount}}, tile.number_token)

    def test_shared_vp_cap_off_by_default_keeps_shipped_rules(self):
        mod = BlightMod(mode="factions")
        self.assertIsNone(mod.shared_vp_cap)
        game, mod, hid, tile = self._shared_cap_game()
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        for _ in range(9):  # 3 tears each -> three milestones, no shared cap
            self._grant_tears(mod, game, hid, tile, 3)
            tile.ext["blight_stage"] = 1
        data = mod._player_blight_state(game, 0)
        self.assertEqual(data["tears_vp"], 3)   # individual cap still 3
        self.assertEqual(data["purify_vp"], 0)
        self.assertEqual(data["alternate_vp"], 3)
        self.assertEqual(game.ext["mod_telemetry"]["blight"]["counters"].get("milestones_after_cap", 0), 0)

    def test_shared_vp_cap_limits_sum_and_keeps_tracking(self):
        game, mod, hid, tile = self._shared_cap_game()
        mod.shared_vp_cap = 4
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        for _ in range(9):  # 27 tears -> 9 milestones; only 4 may mint VP
            self._grant_tears(mod, game, hid, tile, 3)
            tile.ext["blight_stage"] = 1
        data = mod._player_blight_state(game, 0)
        self.assertEqual(data["tears_vp"], 3)          # individual cap
        self.assertEqual(data["alternate_vp"], 3)      # below shared cap 4
        # Now cross to the OTHER track: cleanses mint the 4th shared point,
        # then keep being tracked but stop minting.
        state = mod._player_blight_state(game, 0)
        state["alignment_score"] = -2
        state["alignment"] = "order"
        state["tears"] = 0
        tile.ext["blight_stage"] = 1
        tile.ext["blight_stage_sources"] = [None]
        for _ in range(6):
            tile.ext["blight_stage"] = 1
            game.players[0].resources[Resource.GRAIN] += 1
            result = mod.handle_purify_hex(game, 0, Action.make("purify_hex", hex_id=hid))
            self.assertTrue(result.success, result.error)
        self.assertEqual(data["purify_vp"], 1)                      # 3 + 1 = shared cap 4
        self.assertEqual(data["alternate_vp"], 4)
        self.assertGreater(data["purify_steps_seen"], 1)            # tracking continues
        counters = game.ext["mod_telemetry"]["blight"]["counters"]
        self.assertGreater(counters.get("milestones_after_cap", 0), 0)
        # Nothing retroactively stripped: earlier tears VP intact.
        self.assertEqual(data["tears_vp"], 3)

    def test_shared_vp_cap_never_exceeds_and_victory_uses_real_vp_only(self):
        game, mod, hid, tile = self._shared_cap_game()
        mod.shared_vp_cap = 4
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        for _ in range(12):
            self._grant_tears(mod, game, hid, tile, 3)
            tile.ext["blight_stage"] = 1
        data = mod._player_blight_state(game, 0)
        self.assertLessEqual(data["tears_vp"] + data["purify_vp"], 4)
        # Victory check counts only actually minted VP: the outpost + capped
        # faction VP — never any un-minted milestone. Direct telemetry
        # mutation bypasses the engine wrapper, so refresh the VP cache first.
        game._cache.clear()
        vp = game.get_victory_points(0)
        self.assertEqual(vp, 1 + data["alternate_vp"] + data["accusation_vp"])

    def test_shared_vp_cap_accusation_vp_is_outside_the_limit(self):
        # Accusation needs the hidden mode; build it directly.
        mod = BlightMod(mode="factions", cult_visibility="hidden", spread_chance=0.0)
        game = HexGame("SHARDCAPACC", seed=8081, points_to_win=20, fog_of_war=False, plugins=[mod])
        game._add_player_internal("О", False)
        game._add_player_internal("К", False)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        mod.shared_vp_cap = 4
        state = mod._player_blight_state(game, 0)
        state["alignment_score"] = -2
        state["alignment"] = "order"
        state["alternate_vp"] = 4            # shared cap exhausted
        cult = mod._player_blight_state(game, 1)
        cult["alignment"] = "cult"
        cult["alignment_score"] = 2
        cult["tears"] = 4
        game.players[0].resources[Resource.WOOD] = 1
        result = game.apply_action(0, Action.make("accuse_cultist", target_player_id=1))
        self.assertTrue(result.success, result.error)
        self.assertEqual(state["accusation_vp"], 1)  # accusation VP outside the shared cap
        self.assertEqual(state["alternate_vp"], 4)

    def test_shared_vp_cap_removes_progress_bonus_from_scoring_when_exhausted(self):
        game, mod, hid, tile = self._shared_cap_game()
        mod.shared_vp_cap = 4
        # Corrupt target: an enemy-held clean hex WITHOUT own buildings.
        enemy_hex = next(h for h in sorted(game.map.hexes)
                         if h != hid and _productive_tile(game, h)
                         and all(game.map.vertices[v].structure_owner is None
                                 for v in game.map.hexes[h].vertex_ids))
        enemy_tile = game.map.hexes[enemy_hex]
        enemy_vertex = game.map.vertices[enemy_tile.vertex_ids[0]]
        enemy_vertex.structure_owner = 1
        enemy_vertex.structure_type = StructureType.OUTPOST
        game.players[0].resources[Resource.WOOL] = 3
        state = mod._player_blight_state(game, 0)
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)   # p0 is Cult
        state["tears_vp"] = 2
        state["purify_vp"] = 2
        state["alternate_vp"] = 4            # shared cap exhausted
        action = Action.make("corrupt_hex", hex_id=enemy_hex)
        ctx = {"my_vp": 0, "leader_vp": 0, "leader_id": 2}
        exhausted = mod.score_ai_action(game, 0, action, dict(ctx))
        mod.shared_vp_cap = None             # same state, shipped rules
        baseline = mod.score_ai_action(game, 0, action, dict(ctx))
        self.assertGreater(baseline, exhausted)  # progress bonus removed under the cap

    def test_cult_and_order_have_separate_three_point_caps(self):
        game, mod, _ = self._game("factions")
        data = mod._player_blight_state(game, 0)
        data["tears"] = 9
        data["purified_count"] = 9
        self.assertEqual(mod._award_progress_vp(game, 0, "tears"), 3)
        self.assertEqual(mod._award_progress_vp(game, 0, "purify"), 3)
        self.assertEqual(data["tears_vp"], 3)
        self.assertEqual(data["purify_vp"], 3)
        self.assertEqual(data["alternate_vp"], 6)

    def test_faction_state_is_in_public_observation(self):
        game, mod, _ = self._game("factions")
        mod._change_alignment(game, 0, +1)
        mod._change_alignment(game, 0, +1)
        state = game.get_state_for_player(0)
        self.assertEqual(state["rules"]["blight_mode"], "factions")
        own = next(p for p in state["players"] if p["player_id"] == 0)
        self.assertEqual(own["blight"]["alignment"], "cult")
        self.assertEqual(state["me"]["blight"]["alignment_score"], 2)


if __name__ == "__main__":
    unittest.main()
