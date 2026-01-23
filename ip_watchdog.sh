#!/bin/bash

# IP Watchdog Script
# Monitors external IP changes and logs them

LOG_FILE="ip_watchdog.log"
CURRENT_IP_FILE="current_ip.txt"

# Function to get current external IP
get_external_ip() {
    # Try multiple services in case one is down
    curl -s https://api.ipify.org || curl -s https://icanhazip.com || curl -s https://ifconfig.me/ip
}

# Function to log messages with timestamp
log_message() {
    echo "$(date '+%Y-%m-%d %H:%M:%S') - $1" | tee -a "$LOG_FILE"
}

# Initialize if first run
if [ ! -f "$CURRENT_IP_FILE" ]; then
    get_external_ip > "$CURRENT_IP_FILE"
    # current_ip=$(cat "$CURRENT_IP_FILE")
    # log_message "Initial IP detected: $current_ip"
    # echo "Initial IP: $current_ip"
fi

# initial ip
current_ip=$(cat "$CURRENT_IP_FILE")
log_message "Initial IP detected: $current_ip"
echo "Initial IP: $current_ip"

# Main monitoring loop
log_message "Starting IP monitoring..."
echo "IP Watchdog started. Monitoring for changes..."
echo "Press Ctrl+C to stop"

while true; do
    current_ip=$(cat "$CURRENT_IP_FILE")
    new_ip=$(get_external_ip)

    if [ "$current_ip" != "$new_ip" ] && [ -n "$new_ip" ]; then
        echo "=========================================="
        echo "ALERT: IP Address Changed!"
        echo "Old IP: $current_ip"
        echo "New IP: $new_ip"
        echo "=========================================="

        log_message "IP CHANGE DETECTED - Old: $current_ip, New: $new_ip"
        echo "$new_ip" > "$CURRENT_IP_FILE"
    fi

    sleep 300  # Check every 5 minutes
done
