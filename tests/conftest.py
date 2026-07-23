from pathlib import Path

# VeighNa selects its writable runtime directory at import time.
Path.cwd().joinpath(".vntrader").mkdir(exist_ok=True)
