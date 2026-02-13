#!/usr/bin/env bash
# Sync only DATA from your LOCAL PC to EC2 (data/, market_data_*/).
# Run from your LOCAL PC in the directory that contains regimetrader/ and amazonkey/.
# Usage: ./regimetrader/sync_data_to_ec2.sh   or   bash regimetrader/sync_data_to_ec2.sh

set -e
EC2_HOST="ec2-user@ec2-13-201-128-77.ap-south-1.compute.amazonaws.com"
KEY="${1:-./amazonkey/arshmacpro.pem}"
# Script may live in regimetrader/; find project root (parent of regimetrader)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
REGIMETRADER="$PROJECT_ROOT/regimetrader"

if [[ ! -f "$KEY" ]]; then
  echo "Key not found: $KEY"
  echo "Usage: $0 [path/to/key.pem]"
  echo "Run from directory containing regimetrader/ and amazonkey/"
  exit 1
fi

cd "$PROJECT_ROOT"

# Sync data/ if it exists
if [[ -d "regimetrader/data" ]]; then
  echo "Syncing data/ ..."
  rsync -avvz --progress -e "ssh -i $KEY" \
    ./regimetrader/data/ \
    "$EC2_HOST:~/regimetrader/data/"
else
  echo "No regimetrader/data/ found, skipping."
fi

# Sync each market_data_* directory if any exist
shopt -s nullglob
for dir in ./regimetrader/market_data_*/; do
  if [[ -d "$dir" ]]; then
    name=$(basename "$dir")
    echo "Syncing $name/ ..."
    rsync -avvz --progress -e "ssh -i $KEY" \
      "$dir" \
      "$EC2_HOST:~/regimetrader/$name/"
  fi
done
shopt -u nullglob

echo "Data sync to EC2 complete."

