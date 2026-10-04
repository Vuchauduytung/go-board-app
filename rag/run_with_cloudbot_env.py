"""Run a module with only the Qdrant / Gemini / Valkey credentials taken from cloud-bot/.env.
Loading the whole file would also pull cloud-bot's QDRANT_COLLECTION and point us at its collection."""
import os, runpy, sys
from dotenv import dotenv_values

KEYS = ('QDRANT_URL', 'QDRANT_API_KEY', 'GEMINI_API_KEY', 'VALKEY_URL', 'VALKEY_TOKEN',
        'GROQ_API_KEY', 'OPENROUTER_API_KEY', 'MISTRAL_API_KEY')
env = dotenv_values(os.environ.get('CLOUD_BOT_ENV', '/home/quessalini/Tung/projects/cloud-bot/.env'))
for k in KEYS:
    if env.get(k) and not os.environ.get(k):
        os.environ[k] = env[k]
print('credentials present:', {k: bool(os.environ.get(k)) for k in KEYS}, file=sys.stderr)
module, sys.argv = sys.argv[1], sys.argv[1:]
runpy.run_module(module, run_name='__main__')
