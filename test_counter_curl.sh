#!/bin/bash
# Manual test using curl to reproduce the optimistic counter bug
#
# Prerequisites: Server must be running (make start)
#
# Run with: bash test_counter_curl.sh

set -e

URL="http://localhost:8002/demos/optimistic-counter/"
COOKIE_FILE="/tmp/djust_test_cookies.txt"

echo "=============================================================================="
echo "OPTIMISTIC COUNTER TEST (curl-based)"
echo "=============================================================================="

# Clean up old cookies
rm -f "$COOKIE_FILE"

# Step 1: Initial GET request to establish session
echo ""
echo "[STEP 1] Initial GET request to establish session"
curl -s -c "$COOKIE_FILE" -o /dev/null -w "Status: %{http_code}\n" "$URL"
echo "✅ Session established"

# Function to make POST request and check response
test_increment() {
    local click_num=$1
    echo ""
    echo "[STEP $((click_num + 1))] POST increment (click #$click_num)"

    # Make POST request
    response=$(curl -s -b "$COOKIE_FILE" -c "$COOKIE_FILE" \
        -X POST \
        -H "Content-Type: application/json" \
        -H "X-CSRFToken: $(grep csrftoken "$COOKIE_FILE" | awk '{print $7}')" \
        -d '{"event": "increment", "params": {}}' \
        "$URL")

    # Parse response
    version=$(echo "$response" | python3 -c "import sys, json; print(json.load(sys.stdin).get('version', 'N/A'))" 2>/dev/null || echo "ERROR")
    has_patches=$(echo "$response" | python3 -c "import sys, json; print('patches' in json.load(sys.stdin))" 2>/dev/null || echo "ERROR")
    has_html=$(echo "$response" | python3 -c "import sys, json; print('html' in json.load(sys.stdin))" 2>/dev/null || echo "ERROR")

    echo "  Version: $version"
    echo "  Has 'patches': $has_patches"
    echo "  Has 'html': $has_html"

    if [ "$has_patches" == "True" ]; then
        patches_count=$(echo "$response" | python3 -c "import sys, json; print(len(json.load(sys.stdin)['patches']))" 2>/dev/null || echo "ERROR")
        echo "  Patches count: $patches_count"

        if [ "$patches_count" == "0" ] && [ "$has_html" == "False" ]; then
            echo "  ❌ FAILED: Empty patches array without html fallback"
            echo "  This is the bug! Client has no actionable data."
            echo ""
            echo "Response:"
            echo "$response" | python3 -m json.tool
            exit 1
        fi
    fi

    if [ "$has_html" == "True" ]; then
        html_length=$(echo "$response" | python3 -c "import sys, json; print(len(json.load(sys.stdin)['html']))" 2>/dev/null || echo "ERROR")
        echo "  HTML length: $html_length chars"
    fi

    # Critical check
    if [ "$has_patches" == "False" ] && [ "$has_html" == "False" ]; then
        echo "  ❌ FAILED: Response has neither 'patches' nor 'html'"
        echo ""
        echo "Response:"
        echo "$response" | python3 -m json.tool
        exit 1
    fi

    echo "  ✅ Click #$click_num successful"
}

# Test 5 clicks
for i in {1..5}; do
    test_increment $i
done

echo ""
echo "=============================================================================="
echo "TEST PASSED: All 5 increments worked correctly!"
echo "=============================================================================="

# Cleanup
rm -f "$COOKIE_FILE"
