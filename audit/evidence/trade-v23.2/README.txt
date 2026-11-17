Независимый аудит, часть 2. Репозиторий и ветки не менялись.
rep_run.py   : копия официального run_ab_trade_v25.py + (1) пути к деревьям, (2) read-only обёртка вокруг
               _best_player_trade (копирует диагностику кандидатов), (3) компактный поток событий, (4) арм A0x,
               (5) параметр seats для игр на 2-3 места. Решения ботов не затрагиваются.
rep_main.py  : python3 rep_main.py A0x|A0|A1|A2 <seed_from-seed_to> <perms|all> out.jsonl
rep_seats.py : игры на 3 места, каждая рука в отдельном процессе, печатает ISOLATION-проверку
Деревья: hf-a0 = дерево ветки + main/ai_brain.py (@3590371); hf-a2 = ветка a879444 (A2 = runtime-пороги).
Макет LAB: lab/tools/{rep_*.py, play_session.py, ai_debugger.py, trade_features_spec.py, websockets/__init__.py (заглушка)}.
Анализ: qc.py (манифесты), recompute.py (официальные метрики из summary.json), an1 (метки, сделка->постройка),
an2/an2b (предсказатели, GroupCV), an3/an3b (кандидаты, повтор решений, пороги), match_test.py (привязка кандидатов).
Пути внутри скриптов жёстко прописаны (/home/claude/...) — замените на свои.
