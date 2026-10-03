import pytest

from rag import llm


@pytest.fixture
def keys(monkeypatch):
    for v in llm.KEYS.values():
        monkeypatch.delenv(v, raising=False)
    monkeypatch.setenv('GEMINI_API_KEY', 'g')
    monkeypatch.setenv('GROQ_API_KEY', 'q')
    monkeypatch.setenv('LLM_CHAIN_BOARD', 'gemini:flash,gemini:lite,mistral:m,groq:oss')


def test_chain_skips_providers_without_key(keys):
    assert llm.chain('board') == [('gemini', 'flash'), ('gemini', 'lite'), ('groq', 'oss')]


def test_falls_back_on_overload_and_quota(keys, monkeypatch):
    calls = []

    def gemini(model, *a):
        calls.append(model)
        raise RuntimeError('503 UNAVAILABLE' if model == 'flash' else '429 RESOURCE_EXHAUSTED')

    def openai(provider, model, *a):
        calls.append(model)
        return '{"answer": "ok", "actions": []}', 42

    monkeypatch.setattr(llm, '_call_gemini', gemini)
    monkeypatch.setattr(llm, '_call_openai', openai)
    r = llm.generate('board', 'sys', [{'role': 'user', 'content': 'q'}], 100, schema={'type': 'OBJECT'})
    assert calls == ['flash', 'lite', 'oss'] and r.model == 'groq:oss' and r.tokens == 42


def test_first_answer_wins(keys, monkeypatch):
    monkeypatch.setattr(llm, '_call_gemini', lambda model, *a: ('xin chào', 7))
    assert llm.generate('board', 's', [], 10).model == 'gemini:flash'


def test_all_failing_raises(keys, monkeypatch):
    def boom(*a):
        raise RuntimeError('down')
    monkeypatch.setattr(llm, '_call_gemini', boom)
    monkeypatch.setattr(llm, '_call_openai', boom)
    with pytest.raises(llm.Unavailable):
        llm.generate('board', 's', [], 10)
