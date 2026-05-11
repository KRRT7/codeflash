import sys
from unittest.mock import patch

import pytest

from codeflash.main import main


def test_help_displays_logo(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sys, "argv", ["codeflash", "--help"]), pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "codeflash.ai" in capsys.readouterr().out


def test_help_short_flag_displays_logo(capsys: pytest.CaptureFixture[str]) -> None:
    with patch.object(sys, "argv", ["codeflash", "-h"]), pytest.raises(SystemExit) as exc_info:
        main()
    assert exc_info.value.code == 0
    assert "codeflash.ai" in capsys.readouterr().out
