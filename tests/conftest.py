import sys
from pathlib import Path

# Add tests/_stubs to sys.path so imports fallback to local stubs when Agentscope
# is not installed in the environment.
_STUBS = Path(__file__).resolve().parent / "_stubs"
sys.path.insert(0, str(_STUBS))

