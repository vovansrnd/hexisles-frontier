"""Cult feedback and tears-source observability (no rule changes).

Everything here is telemetry and UX: the awarded tears must equal the sum of
the recorded sources, notifications must match what was actually granted, and
hidden-mode information must not leak through public channels or observations.
"""
import unittest

from isles_core import Action, GamePhase, HexGame, Resource, StructureType
from mods_pack import BlightMod, _productive_tile, _telemetry


def _tel(game, pid):
    return _telemetry(game, "blight")["by_player"].get(str(pid), {})


class BlightFeedbackTests(unittest.TestCase):
    def _game(self, hidden=False, cult_corrupt_tear=False):
        mod = BlightMod(
            mode="factions", spread_chance=0.0,
            cult_visibility="hidden" if hidden else "open",
            cult_corrupt_tear=cult_corrupt_tear,
        )
        game = HexGame(
            "FEEDBACK", points_to_win=20, seed=7319,
            fog_of_war=False, plugins=[mod],
        )
        for name in ("Attacker", "Victim", "Third"):
            self.assertTrue(game._add_player_internal(name, False).success)
        self.assertTrue(game.start_game().success)
        game.phase = GamePhase.MAIN
        game.current_player_id = 0
        game.global_turn = 5
        for pid in (0, 1, 2):
            for resource in (Resource.WOOD, Resource.CLAY, Resource.GRAIN, Resource.WOOL, Resource.ORE):
                game.players[pid].resources[resource] = 20
        # A productive hex shared by attacker (0) and victim (1) buildings.
        for hid in sorted(game.map.hexes):
            tile = game.map.hexes[hid]
            if not _productive_tile(game, hid) or len(tile.vertex_ids) < 2:
                continue
            tile.ext["blight_stage"] = 1
            tile.ext["blight_stage_sources"] = [0]
            v0, v1 = tile.vertex_ids[0], tile.vertex_ids[1]
            game.map.vertices[v0].structure_type = StructureType.OUTPOST
            game.map.vertices[v0].structure_owner = 0
            game.map.vertices[v1].structure_type = StructureType.OUTPOST
            game.map.vertices[v1].structure_owner = 1
            return game, mod, hid
        raise AssertionError("no suitable hex on the test map")

    def _make_cult(self, mod, game, pid=0):
        state = mod._player_blight_state(game, pid)
        state["alignment_score"] = 2
        state["alignment"] = "cult"

    def test_suppression_feedback_matches_the_award(self):
        game, mod, hid = self._game()
        self._make_cult(mod, game)
        before = int(mod._player_blight_state(game, 0)["tears"])
        mod.modify_resource_gains(game, {1: {game.map.hexes[hid].resource: 2}}, int(game.map.hexes[hid].number_token))
        state0 = mod._player_blight_state(game, 0)
        awarded = int(state0["tears"]) - before
        # Stage 1 on the shared hex: the attacker's own building is immune
        # (+1 immunity tear) and the victim loses one unit (+1 suppression tear).
        self.assertEqual(awarded, 2)
        feedback = state0["feedback"]
        suppression = [f for f in feedback if f["kind"] == "suppression"]
        self.assertEqual(len(suppression), 1)
        self.assertIn("вам 1 слезу", suppression[-1]["text"])
        # Progress tail matches the threshold arithmetic at the award moment
        # (the suppression tear lands before the immunity one: tears were 1).
        self.assertIn("осталось 2", suppression[-1]["text"])
        self.assertEqual(int(_tel(game, 0).get("cult_suppression_tears", 0)), 1)
        # The immunity award also reported itself, as the latest entry.
        self.assertEqual(feedback[-1]["kind"], "immunity")

    def test_suppression_event_records_breakdown_once(self):
        game, mod, hid = self._game()
        tile = game.map.hexes[hid]
        tile.ext["blight_stage"] = 2
        tile.ext["blight_stage_sources"] = [0, 1]  # two different attackers
        mod.modify_resource_gains(game, {1: {tile.resource: 3}}, int(tile.number_token))
        events = [e for e in _telemetry(game, "blight")["events"] if e.get("kind") == "suppress"]
        self.assertEqual(len(events), 1, "one event per (victim, resource, roll)")
        event = events[0]
        # Stage 2, but the victim's single outpost yields 1: exactly one unit
        # is lost, attributed to the FIRST stage source only (no duplication).
        self.assertEqual(int(event["amount"]), 1)
        self.assertEqual(event["by_player"], {0: 1})
        self.assertEqual(int(event["natural"]), 0)
        self.assertEqual(event["resource"], tile.resource.value)

    def test_tear_sources_add_up_to_total(self):
        game, mod, hid = self._game(cult_corrupt_tear=True)
        self._make_cult(mod, game)
        # Direct: a corruption pays one tear immediately.
        victim_hex = next(h for h in sorted(game.map.hexes)
                          if h != hid and _productive_tile(game, h)
                          and all(game.map.vertices[v].structure_owner != 0
                                  for v in game.map.hexes[h].vertex_ids))
        game.current_player_id = 0
        self.assertTrue(mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=victim_hex)).success)
        # Immunity + suppression: one roll on the shared hex.
        mod.modify_resource_gains(game, {1: {game.map.hexes[hid].resource: 2}}, int(game.map.hexes[hid].number_token))
        state0 = mod._player_blight_state(game, 0)
        sources = sum(int(state0.get(k, 0)) for k in
                      ("tears_from_immunity", "tears_from_suppression", "tears_from_direct"))
        self.assertEqual(sources, int(state0["tears"]))
        self.assertEqual(int(state0["tears_from_direct"]), 1)
        self.assertEqual(int(state0["tears_from_suppression"]), 1)
        self.assertEqual(int(state0["tears_from_immunity"]), 1)
        self.assertEqual(sources, 3)

    def test_hidden_mode_keeps_feedback_personal(self):
        game, mod, hid = self._game(hidden=True)
        self._make_cult(mod, game)
        mod.modify_resource_gains(game, {1: {game.map.hexes[hid].resource: 2}}, int(game.map.hexes[hid].number_token))
        # Personal queues are filled for both sides…
        self.assertTrue(mod._player_blight_state(game, 0)["feedback"])
        self.assertTrue(mod._player_blight_state(game, 1)["feedback"])
        # …but the public journal carries no suppression-tears announcement and
        # the victim's personal text names no attacker.
        self.assertFalse(any("Скверна подавила" in entry.get("msg", "") for entry in game.log))
        self.assertNotIn("Attacker", mod._player_blight_state(game, 1)["feedback"][-1]["text"])
        # Other players' observations expose no source split or progress.
        obs = game.get_state_for_player(2)
        other = next(p for p in obs["players"] if int(p["player_id"]) == 0)
        self.assertIsNone(other["blight"]["tears_from_suppression"])
        self.assertIsNone(other["blight"]["tears_vp"])
        me = obs["me"]["blight"]
        self.assertEqual(int(me["tears_from_suppression"]), 0)
        self.assertIn("feedback", me)

    def test_open_mode_exposes_progress_for_others(self):
        game, mod, hid = self._game(hidden=False)
        self._make_cult(mod, game)
        mod.modify_resource_gains(game, {1: {game.map.hexes[hid].resource: 2}}, int(game.map.hexes[hid].number_token))
        obs = game.get_state_for_player(2)
        other = next(p for p in obs["players"] if int(p["player_id"]) == 0)
        self.assertEqual(int(other["blight"]["tears_from_suppression"]), 1)
        self.assertEqual(int(other["blight"]["tears_vp_step"]), 3)
        self.assertIn("Скверна подавила", " ".join(entry.get("msg", "") for entry in game.log))

    def test_cap_note_explicitly_stops_promising_points(self):
        game, mod, hid = self._game()
        self._make_cult(mod, game)
        state0 = mod._player_blight_state(game, 0)
        state0["tears"] = 8
        state0["tears_vp"] = 3
        state0["alternate_vp"] = 3
        state0["tears_steps_seen"] = 2
        mod.modify_resource_gains(game, {1: {game.map.hexes[hid].resource: 2}}, int(game.map.hexes[hid].number_token))
        text = mod._player_blight_state(game, 0)["feedback"][-1]["text"]
        self.assertIn("предел +3", text)
        self.assertIn("больше не начисляются", text)
        self.assertEqual(int(mod._player_blight_state(game, 0)["tears_vp"]), 3)

    def test_rules_are_untouched_by_feedback_patch(self):
        game, mod, hid = self._game()
        # Corruption still costs exactly one wool and no tear is granted with
        # the direct reward off (shipped rules). Target: a clean hex without
        # own buildings (the shared hex hosts the attacker's outpost).
        player = game.players[0]
        player.resources = {Resource.WOOL: 1, Resource.WOOD: 0, Resource.CLAY: 0,
                            Resource.GRAIN: 0, Resource.ORE: 0}
        clean = next(h for h in sorted(game.map.hexes)
                     if h != hid and _productive_tile(game, h)
                     and all(game.map.vertices[v].structure_owner != 0
                             for v in game.map.hexes[h].vertex_ids))
        self.assertTrue(mod.handle_corrupt_hex(game, 0, Action.make("corrupt_hex", hex_id=clean)).success)
        self.assertEqual(int(player.resources.get(Resource.WOOL, 0)), 0)
        state0 = mod._player_blight_state(game, 0)
        self.assertEqual(int(state0["tears"]), 0)
        self.assertEqual(sum(int(state0.get(k, 0)) for k in
                             ("tears_from_immunity", "tears_from_suppression", "tears_from_direct")), 0)
        # The suppression event itself carries no rule weight: winners of the
        # roll are unchanged (the victim simply got fewer resources).
        self.assertEqual(int(_tel(game, 0).get("cult_suppression_tears", 0)), 0)


if __name__ == "__main__":
    unittest.main()
