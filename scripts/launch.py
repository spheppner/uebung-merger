"""Einstiegspunkt für PyInstaller (absolute Importe, damit das Paket gefunden wird)."""

import sys

from uebung_merger.app import main

sys.exit(main())
