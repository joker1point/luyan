"""
[测试] API Key 存储防御：masked 串不能覆盖真 key
- 目标: services/llm_settings_store.py 的 _is_masked_key 防御
- 方法: 直接调 LLMSettingsStore.update_provider()，传 masked 串验证
       cfg['api_key'] 是否被覆盖
"""
import os
import sys
import json
import shutil
import tempfile

# 强制切到临时目录
TMP = tempfile.mkdtemp(prefix="llm_test_")
print(f"[Setup] tmpdir = {TMP}")

# 1. 改写 _SETTINGS_DIR + _SETTINGS_FILE，让 store 写到 tmpdir
sys.path.insert(0, r'c:\Users\biren\Documents\trae_projects\luyan\CharacterSeed\backend')
from services import llm_settings_store as store_mod

# 改模块内全局路径
store_mod._SETTINGS_DIR = TMP
store_mod._SETTINGS_FILE = os.path.join(TMP, "llm_settings.json")
store_mod._cache = None
# 进程内 file_lock 留着，但所有读写都打 tmpdir

print(f"[Setup] override path = {store_mod._SETTINGS_FILE}")

from services.llm_settings_store import LLMSettingsStore

store = LLMSettingsStore()

def get_qwen_key():
    return store.get_all()["providers"]["qwen"].get("api_key")

# 2. 写入真 key
REAL_KEY = "sk-real-test-1234567890abcdef"
store.update_provider("qwen", api_key=REAL_KEY)
cfg = store.get_all()["providers"]["qwen"]
print(f"[Step 1] 写入真 key, cfg.api_key = {cfg.get('api_key')}")
assert cfg.get("api_key") == REAL_KEY, "init write failed"

# 3. 用 mask_api_key 算一个脱敏串，再"假装是用户输入"回写
masked = LLMSettingsStore.mask_api_key(REAL_KEY)
print(f"[Step 2] mask_api_key(REAL_KEY) = {masked!r}")
assert "*" in masked and len(masked) >= 8, "masked format unexpected"

# 4. 调 update_provider 传 masked 串
result = store.update_provider("qwen", api_key=masked)
print(f"[Step 3] update_provider(masked) -> result.api_key = {result.get('api_key')}")
print(f"[Step 3] update_provider(masked) -> result.base_url = {result.get('base_url')}")

# 5. 验证：真 key 应保留
cfg2 = store.get_all()["providers"]["qwen"]
final_key = cfg2.get("api_key")
print(f"[Step 4] 再读 cfg.api_key = {final_key}")

if final_key == REAL_KEY:
    print("PASS: masked 串被识别并跳过，磁盘上的真 key 未被覆盖")
else:
    print(f"FAIL: 真 key 被覆盖为 {final_key!r}")
    sys.exit(1)

# 6. 验证：合法的新 key 仍然能写入（不误伤）
NEW_REAL = "sk-new-real-9876543210abcdef"
store.update_provider("qwen", api_key=NEW_REAL)
cfg3 = store.get_all()["providers"]["qwen"]
print(f"[Step 5] update_provider(NEW_REAL) -> cfg.api_key = {cfg3.get('api_key')}")
if cfg3.get("api_key") == NEW_REAL:
    print("PASS: 合法新 key 正常写入")
else:
    print(f"FAIL: 合法新 key 写入失败 = {cfg3.get('api_key')!r}")
    sys.exit(1)

# 7. 验证：base_url 空串 → 兜底默认
store.update_provider("qwen", api_key=None, base_url="", model="")
cfg4 = store.get_all()["providers"]["qwen"]
print(f"[Step 6] 空 base_url/model -> {cfg4}")
if cfg4.get("base_url") and cfg4.get("model"):
    print("PASS: 空值自动兜底为 PROVIDER_DEFAULTS")
else:
    print(f"FAIL: 兜底逻辑异常: {cfg4}")
    sys.exit(1)

# 8. 磁盘文件确认
disk_path = store_mod._SETTINGS_FILE
print(f"[Step 7] 磁盘文件 {disk_path}:")
with open(disk_path) as f:
    disk = json.load(f)
print(json.dumps(disk, indent=2, ensure_ascii=False))

# 清理
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n[Cleanup] removed {TMP}")
print("\n[OK] all 单元测试通过")
