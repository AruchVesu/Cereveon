def build_player_timeline(game_records):
    skill = 1200
    confidence = 0.5
    fatigue = 0.2

    timeline = []

    for r in game_records:
        delta_skill = -0.5 if r["is_blunder"] else 0.1

        timeline.append((skill, confidence, fatigue))

        skill += delta_skill
        confidence = max(0.0, min(1.0, confidence + delta_skill * 0.01))

    return timeline
