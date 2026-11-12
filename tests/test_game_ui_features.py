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

    def test_v3_incoming_trade_names_sender_and_has_single_directional_display(self):
        app = (ROOT / "static_local_v3" / "app.js").read_text(encoding="utf-8")
        self.assertIn('`Предложение от ${partnerName}`', app)
        self.assertIn('const proposer = (gameState.players || []).find', app)
        self.assertIn('head.appendChild(timeLabel);\n    // The incoming-offer branch must attach the populated header to the card.\n    // Without this append, the sender name/avatar exist only in a detached node.\n    card.appendChild(head);\n    const deal = node("div", "offer-deal");', app)
        self.assertNotIn('ПРЕДЛОЖЕНИЕ ОТ ИГРОКА', app)
        self.assertIn('offerSide("ОТДАЁШЬ", offer.want || {}, "give")', app)
        self.assertIn('offerSide("ПОЛУЧАЕШЬ", offer.give || {}, "get")', app)
        self.assertIn('classList.toggle("trade-dialog-open"', app)
        self.assertNotIn("trade-summary", app)
        self.assertIn('node("span", "trade-ava"', app)

    def test_v4_audio_context_waits_for_a_user_gesture(self):
        fx = (ROOT / "static_v4" / "js" / "fx.js").read_text(encoding="utf-8")
        main = (ROOT / "static_v4" / "js" / "main.js").read_text(encoding="utf-8")
        self.assertIn("setSound(on, fromUserGesture = false)", fx)
        self.assertIn("if (this.enabled && fromUserGesture) this.unlockAudio();", fx)
        self.assertIn('document.addEventListener("pointerdown", unlockAfterGesture, true)', fx)
        self.assertIn("applySettings(ui, true)", main)

    def test_v4_incoming_trade_names_sender_and_has_directional_arrow(self):
        hud = (ROOT / "static_v4" / "js" / "ui" / "hud.js").read_text(encoding="utf-8")
        css = (ROOT / "static_v4" / "css" / "ui.css").read_text(encoding="utf-8")
        self.assertIn('`${proposerName} предлагает обмен`', hud)
        self.assertIn('class: "offer-proposer-dot"', hud)
        self.assertIn('side(wr, wn, "ОТДАЁШЬ")', hud)
        self.assertIn('side(gr, gn, "ПОЛУЧАЕШЬ")', hud)
        self.assertIn('class: "offer-arrow"', hud)
        self.assertIn("function render(state)", hud)
        self.assertIn("renderOffer(g);", hud)
        self.assertIn("return { render, offerHost };", hud)
        self.assertNotIn("trade-summary", hud)
        self.assertIn(".offer-proposer-dot", css)

    def test_cult_visibility_and_accusation_are_available_in_both_clients(self):
        v3_html = (ROOT / "static_local_v3" / "index.html").read_text(encoding="utf-8")
        v3_app = (ROOT / "static_local_v3" / "app.js").read_text(encoding="utf-8")
        v4_lobby = (ROOT / "static_v4" / "js" / "ui" / "lobby.js").read_text(encoding="utf-8")
        v4_dock = (ROOT / "static_v4" / "js" / "ui" / "dock.js").read_text(encoding="utf-8")
        self.assertIn('id="cult-visibility"', v3_html)
        self.assertIn('cult_visibility:', v3_app)
        self.assertIn('accuse_cultist', v3_app)
        self.assertIn('if (blight.alignment !== "hidden") meta.appendChild(node("span", "blight-player-vp"', v3_app)
        self.assertIn('cultVisibility: "open"', v4_lobby)
        self.assertIn('cult_visibility: state.cultVisibility', v4_lobby)
        self.assertIn('filter((a) => a.type === "accuse_cultist")', v4_dock)

    def test_v4_routes_can_be_enabled_previewed_and_activated(self):
        lobby = (ROOT / "static_v4" / "js" / "ui" / "lobby.js").read_text(encoding="utf-8")
        side = (ROOT / "static_v4" / "js" / "ui" / "side.js").read_text(encoding="utf-8")
        board = (ROOT / "static_v4" / "js" / "board.js").read_text(encoding="utf-8")
        css = (ROOT / "static_v4" / "css" / "board.css").read_text(encoding="utf-8")
        self.assertIn('sw("routes", "Торговые маршруты"', lobby)
        self.assertIn('routes: state.routes', lobby)
        self.assertIn('a.type === "activate_route"', side)
        self.assertIn('handlers.onActivateRoute(action)', side)
        self.assertIn('previewRoute(path)', board)
        self.assertIn('.route-highlight-active', css)
        self.assertIn('routes', (ROOT / "server_local_v4.py").read_text(encoding="utf-8"))

    def test_v4_has_dark_theme_only_and_compact_four_player_mobile_layout(self):
        root = ROOT / "static_v4"
        main = (root / "js" / "main.js").read_text(encoding="utf-8")
        modal = (root / "js" / "ui" / "modals.js").read_text(encoding="utf-8")
        theme = (root / "css" / "theme.css").read_text(encoding="utf-8")
        layout = (root / "css" / "layout.css").read_text(encoding="utf-8")
        self.assertIn('theme: "night"', main)
        self.assertIn('dataset.theme = "night"', main)
        self.assertNotIn('"Тема"', modal)
        self.assertNotIn('data-theme="parchment"', theme)
        self.assertIn('grid-template-columns: repeat(4, minmax(0, 1fr))', layout)
        self.assertIn('grid-template-columns: repeat(5, minmax(0, 1fr))', layout)
        self.assertIn('grid-template-columns: repeat(4, minmax(0, 1fr))', layout)

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
