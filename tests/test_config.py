from backend.config import ROOT, Settings


def test_example_environment_is_a_valid_default():
    config = Settings(_env_file=ROOT / ".env.example")
    assert config.app_mode == "demo"
    assert config.llm_input_price_per_million is None
    assert not config.llm_ready
