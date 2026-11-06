import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from charvar.models import SubjectProfile  # noqa: E402


@pytest.fixture(autouse=True)
def _isolate_openrouter_env(monkeypatch):
    """清掉仓库 .env 的真实凭据，防测试里误打真请求 / 断言里混进真实 key。

    各用例在 fixture 之后自己 setenv 的值不受影响（monkeypatch 优先级正常）。
    """
    for key in (
        "OP_API_KEY",
        "OP_BASE_URL",
        "OP_IMAGE_API_KEY",
        "LLM_API_KEY",
        "LLM_BASE_URL",
        "LLM_MODEL",
    ):
        monkeypatch.delenv(key, raising=False)


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
