#!/bin/bash
# Start tracker in background
python tracker.py &

# Start server in foreground (so container stays alive)
python server.py
