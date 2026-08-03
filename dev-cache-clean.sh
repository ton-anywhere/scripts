#!/bin/bash
echo "Cleaning pip..."
pip cache purge

echo "Cleaning uv..."
uv cache clean

echo "Cleaning npm..."
npm cache clean --force

echo "Cleaning pip..."
find /home/airtonp/.cache/node -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

echo "Cleaning rubocop..."
find /home/airtonp/.cache/rubocop_cache -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

echo "Cleaning thumbnails..."
find /home/airtonp/.cache/thumbnails -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

echo "Cleaning sublime..."
find /home/airtonp/.cache/sublime-text-3 -mindepth 1 -maxdepth 1 -exec rm -rf -- {} +

echo "Cleaning cargo..."
cd /home/airtonp/code
cargo-clean-all

echo "Finished"

