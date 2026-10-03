"""Check every model of the LLM chains with a short Vietnamese question and a JSON answer.
   python -m rag.run_with_cloudbot_env rag.probe_llm      (keys from cloud-bot/.env and the environment)"""
import time

from rag import llm

seen = set()
for name in llm.CHAINS:
    for provider, model in llm.chain(name):
        if (provider, model) in seen:
            continue
        seen.add((provider, model))
        t = time.time()
        try:
            text, tokens = (llm._call_gemini(model, 'Trả lời bằng tiếng Việt.', [{'role': 'user', 'content': 'Cờ vây có bao nhiêu giao điểm trên bàn 19x19? Trả lời JSON {"answer": "..."}'}], 200, {'type': 'OBJECT', 'properties': {'answer': {'type': 'STRING'}}}, None, 'low')
                            if provider == 'gemini' else
                            llm._call_openai(provider, model, 'Trả lời bằng tiếng Việt, dạng JSON {"answer": "..."}.', [{'role': 'user', 'content': 'Cờ vây có bao nhiêu giao điểm trên bàn 19x19?'}], 200, {'type': 'OBJECT'}, None))
            print(f'OK   {provider}:{model} {time.time() - t:.1f}s {tokens} tokens: {text[:80]!r}')
        except Exception as ex:
            print(f'FAIL {provider}:{model} {time.time() - t:.1f}s: {str(ex)[:160]}')
missing = [p for p, v in llm.KEYS.items() if not __import__('os').environ.get(v)]
print('no API key (skipped):', ', '.join(missing) or '-')
