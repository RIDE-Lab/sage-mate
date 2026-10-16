from types import SimpleNamespace
import pytest
from tools.check_retrieval_config import FIELDS, differences, process_environment, projection


def test_matching_settings():
    settings = SimpleNamespace(**{name:'same' for name in FIELDS})
    assert differences(projection(settings), projection(settings)) == []


def test_only_effective_embedding_endpoint_matters():
    settings = SimpleNamespace(**{name:'same' for name in FIELDS})
    settings.llm_base_url = 'unused-chat-endpoint'
    before = projection(settings)
    settings.llm_base_url = 'different-unused-chat-endpoint'
    assert differences(before, projection(settings)) == []
    settings.sagevdb_embedding_base_url = ''
    assert projection(settings)['effective_embedding_endpoint'] == settings.llm_base_url


@pytest.mark.parametrize('field', FIELDS)
def test_each_config_field_is_guarded(field):
    current = dict.fromkeys(FIELDS, 'same')
    expected = {**current, field:'different'}
    assert differences(current, expected) == [field]


def test_process_environment_requires_twin(tmp_path):
    process = tmp_path / '123'
    process.mkdir()
    (process / 'cmdline').write_bytes(b'python\0other.py\0')
    with pytest.raises(RuntimeError):
        process_environment(123, tmp_path)
    with pytest.raises(RuntimeError):
        process_environment(0, tmp_path)


def test_process_environment_preserves_secret_without_logging(tmp_path, capsys):
    process = tmp_path / '123'
    process.mkdir()
    (process / 'cmdline').write_bytes(b'/python\0-m\0uvicorn\0sage_faculty_twin.api:app\0')
    (process / 'environ').write_bytes(b'EXAMPLE_KEY=not-a-real-key\0A=b=c\0')
    executable, environment, cwd = process_environment(123,tmp_path)
    assert executable == '/python'
    assert environment['A'] == 'b=c'
    assert environment['EXAMPLE_KEY'] == 'not-a-real-key'
    assert capsys.readouterr().out == ''
