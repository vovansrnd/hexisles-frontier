#!/bin/bash
# HexIsles — запуск локального сервера с клиентом V5 (карта на весь экран).
# Один порт обслуживает и клиент, и WebSocket.
# Порт по умолчанию: 8080. Если занят: HEXISLES_PORT=8090 ./run_local_v5.sh

cd "$(dirname "$0")"
HEXISLES_STATIC=static_v5 exec python3 server_local_v4.py
