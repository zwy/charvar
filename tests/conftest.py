import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from charvar.models import SubjectProfile  # noqa: E402


@pytest.fixture()
def silver_witch() -> SubjectProfile:
    return SubjectProfile(
        id="silver_witch",
        name="银白女巫",
        subject_noun="woman",
        trigger_word="sw1tch",
        reference_tag="<image1>",
        style_lock=["the original illustration style", "the color palette"],
        identity_lock=["long silver-white hair", "the same face and eye color", "the same physique"],
        outfit_lock=["the black witch robe and pointed hat"],
        extra="",
    )
