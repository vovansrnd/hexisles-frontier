from tools.analyze_blight_archive import rows_for_match, aggregate


def test_archive_analysis_splits_blight_vp_and_counts_actions():
    bundle = {
        "match": {
            "match_id": "M1", "finished_at": "2026-10-26T00:00:00Z", "room_id": "R1",
            "global_turns": 96, "finish_reason": "victory_points", "points_to_win": 12,
            "config": {"local_server_config": {"blight_mode": "factions", "cult_visibility": "open"}},
            "final_state": {
                "players": {"0": {"victory_points_total": 12}},
                "extensions": {"blight": {"players": {"0": {
                    "alignment": "cult", "alignment_score": 3, "tears_vp": 2,
                    "purify_vp": 1, "alternate_vp": 3, "accusation_vp": 1,
                    "tears": 9, "purified_count": 6,
                }}}},
            },
            "final_mod_telemetry": {"blight": {"by_player": {"0": {"alignment_changes": 5}}}},
        },
        "players": [{
            "player_id": 0, "name": "Player", "is_bot": False, "bot_type": None,
            "bot_archetype": None, "is_winner": True, "rank": 1, "victory_points_total": 12,
        }],
        "events": [
            {"event_type": "action", "actor_id": 0, "action_type": "corrupt_hex", "success": True},
            {"event_type": "action", "actor_id": 0, "action_type": "purify_hex", "success": True},
            {"event_type": "action", "actor_id": 0, "action_type": "accuse_cultist", "success": False},
        ],
    }
    rows = rows_for_match(bundle)
    assert len(rows) == 1
    row = rows[0]
    assert row["base_vp_excluding_blight"] == 8
    assert row["alternate_vp"] == 3
    assert row["accusation_vp"] == 1
    assert row["corrupt_hex_actions"] == 1
    assert row["purify_hex_actions"] == 1
    assert row["accuse_actions"] == 0
    assert row["failed_actions"] == 1
    assert row["alignment_changes"] == 5
    summary = aggregate(rows, [{"global_turns": 96}])
    assert summary["matches"] == 1
    assert summary["winners_by_bot_type"] == {"human": 1}
