#!/usr/bin/env python3
"""Serenity QA -- Run with: python Serenity.py --url https://example.com --live"""

import sys
import os

# Add src to path so serenity package is importable
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from serenity.cli import main

if __name__ == "__main__":
    main()
