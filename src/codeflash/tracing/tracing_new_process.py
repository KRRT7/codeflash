from __future__ import annotations

import warnings
from typing import TYPE_CHECKING


# Suppress dill PicklingWarning
warnings.filterwarnings("ignore", message="Cannot locate reference to")
warnings.filterwarnings("ignore", message="Cannot pickle.*recursive self-references")

if TYPE_CHECKING:
    pass


