import os
import sys
sys.path.insert(0, r"C:\Users\biren\Documents\trae_projects\luyan\CharacterSeed")

# Load .env manually
env_path = r"C:\Users\biren\Documents\trae_projects\luyan\CharacterSeed\.env"
if os.path.exists(env_path):
    with open(env_path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip())

print("AGNES_API_KEY set:", bool(os.environ.get("AGNES_API_KEY")))
print("QWEN_API_KEY set:", bool(os.environ.get("QWEN_API_KEY")))

from backend.services.llm_service import LLMService

llm = LLMService()
print("self._active_provider_id:", llm._active_provider_id)
print("self._task_routing:", llm._task_routing)
print('routing.chat:', llm._task_routing.get('chat', 'MISSING'))
print('cache keys before:', list(llm._PROVIDER_CACHE.keys()))

prov = llm._resolve_task_provider('chat')
print('after task=chat, provider:', prov['provider'], 'model:', prov['model'])
print('cache keys after:', list(llm._PROVIDER_CACHE.keys()))

prov2 = llm._resolve_task_provider('creation')
print('after task=creation, provider:', prov2['provider'], 'model:', prov2['model'])
print('cache keys final:', list(llm._PROVIDER_CACHE.keys()))
