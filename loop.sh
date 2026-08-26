#!/bin/bash

while true; do
    bash /app/playlist_builder.sh
    bash /app/concat_playlist.sh
    bash /app/streamer.sh
done
