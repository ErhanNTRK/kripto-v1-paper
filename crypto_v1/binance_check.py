"""Read-only credential check. This module cannot place orders."""
import json
from .binance_account import verify_from_environment

def main():
    print(json.dumps(verify_from_environment(), sort_keys=True))

if __name__ == "__main__":
    main()
