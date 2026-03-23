#!/bin/bash
OLD_USER="APonce911"
NEW_USER="ton-anywhere"

if git remote get-url fork &>/dev/null; then
  url=$(git remote get-url fork)
  new_url="${url/$OLD_USER/$NEW_USER}"
  git remote set-url fork "$new_url"
  echo "fork updated: $new_url"
else
  url=$(git remote get-url origin)
  new_url="${url/$OLD_USER/$NEW_USER}"
  git remote set-url origin "$new_url"
  echo "origin updated: $new_url"
fi
