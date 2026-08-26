#!/bin/bash

PLAYLIST="/app/playlist/playlist.txt"
FINAL="/app/playlist/final.mp4"

ffmpeg -y -f concat -safe 0 -i "$PLAYLIST" -c copy "$FINAL"
