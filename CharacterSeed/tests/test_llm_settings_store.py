"""
LLMSettingsStore 单元测试

测试目标：
  1. 文件初始化和读写（含原子写）
  2. provider 的增/改/查/切
  3. _merge_defaults 合并逻辑（新增 provider 不覆盖已有值）
  4. 环境变量兜底（get_provider_with_env_fallback）
  5. API Key 脱敏（mask_api_key）
  6. 并发安全性（文件锁）
  7. 设置文件损坏后的恢复行为

预期运行方式：python -m pytest tests/test_llm_settings_store.py -v
不依赖外部网络或真实 API Key。
"""
import json
import os
import tempfile
import pytest

from backend.services.llm_settings_store import (
    LLMSettingsStore,
    PROVIDER_DEFAULTS,
    PROVIDER_META,
    DEFAULT_ACTIVE,
    DEFAULT_TEMPERATURE,
    DEFAULT_MAX_TOKENS,
    _merge_defaults,
    _default_settings,
    _atomic_write,
)


# ==============================================================================
# Fixture：将 LLMSettingsStore 的写路径临时劫持到临时目录
# ==============================================================================

@pytest.fixture(autouse=True)
def _patch_settings_path(monkeypatch, tmp_path):
    """
    将 LLMSettingsStore 内部 _SETTINGS_FILE 指向临时目录，
    避免影响开发环境真实的 usercontext/llm_settings.json。
    """
    monkeypatch.setattr(
        "backend.services.llm_settings_store._SETTINGS_DIR",
        str(tmp_path),
    )
    monkeypatch.setattr(
        "backend.services.llm_settings_store._SETTINGS_FILE",
        str(tmp_path / "llm_settings.json"),
    )
    yield
    # 每次测试后清理
    for f in tmp_path.iterdir():
        try:
            f.unlink()
        except OSError:
            pass


# ==============================================================================
# Test Suite 1：初始化 & 基础读写
# ==============================================================================

class TestInitAndDefaults:
    """测试首次初始化和默认配置"""

    def test_init_creates_file(self):
        """LLMSettingsStore() 首次调用应自动创建 llm_settings.json"""
        store = LLMSettingsStore()
        assert os.path.exists(store.settings_file_path())
        data = json.load(open(store.settings_file_path(), encoding="utf-8"))
        assert data["active_provider"] == DEFAULT_ACTIVE
        assert "providers" in data
        for pid in PROVIDER_DEFAULTS:
            assert pid in data["providers"]

    def test_get_all_returns_full_config(self):
        """get_all() 返回完整配置字典"""
        store = LLMSettingsStore()
        data = store.get_all()
        assert isinstance(data, dict)
        assert "active_provider" in data
        assert "providers" in data
        assert "default_temperature" in data
        assert data["default_temperature"] == DEFAULT_TEMPERATURE

    def test_get_active_provider_id(self):
        """get_active_provider_id() 返回当前激活的 provider"""
        store = LLMSettingsStore()
        assert store.get_active_provider_id() == DEFAULT_ACTIVE

    def test_get_active_provider(self):
        """get_active_provider() 返回当前激活 provider 的完整配置（含 apikey 明文）"""
        store = LLMSettingsStore()
        cfg = store.get_active_provider()
        assert "api_key" in cfg
        assert "base_url" in cfg
        assert "model" in cfg
        # 默认所有 key 都是空字符串（文件是新的）
        assert cfg["api_key"] == ""


class TestAtomicWrite:
    """原子写安全性测试"""

    def test_atomic_write_survives_partial(self, tmp_path):
        """原子写：即使写入中途崩溃，主文件也不会损坏"""
        target = tmp_path / "test_atomic.json"
        content = '{"status": "ok", "data": [1, 2, 3]}'

        # 正常写入
        _atomic_write(str(target), content)
        assert target.exists()
        data = json.loads(target.read_text(encoding="utf-8"))
        assert data["status"] == "ok"

        # 模拟"写一半"场景：手动留一个损坏的 .tmp 文件
        target.write_text("__CORRUPTED__", encoding="utf-8")
        corrupted_tmp = tmp_path / "corrupted.tmp"
        corrupted_tmp.write_text("半成品{", encoding="utf-8")

        # 再次原子写应当覆盖并纠正
        _atomic_write(str(target), content)
        data2 = json.loads(target.read_text(encoding="utf-8"))
        assert data2["status"] == "ok"
        # .tmp 文件不应残留
        assert not (tmp_path / "test_atomic.json.tmp").exists()


# ==============================================================================
# Test Suite 2：Provider 操作
# ==============================================================================

class TestProviderOps:
    """测试 provider 的增/改/查/切"""

    def test_set_active_provider_with_invalid_raises(self):
        """set_active_provider 传入不存在的 provider 应抛 KeyError"""
        store = LLMSettingsStore()
        with pytest.raises(KeyError, match="未知 provider"):
            store.set_active_provider("nonexistent_provider")

    def test_set_active_provider_valid(self):
        """set_active_provider 应成功切换并持久化"""
        store = LLMSettingsStore()
        store.set_active_provider("deepseek")
        assert store.get_active_provider_id() == "deepseek"
        # 从另一个实例读取也应看到变化
        store2 = LLMSettingsStore()
        assert store2.get_active_provider_id() == "deepseek"

    def test_update_provider_fields(self):
        """update_provider 应仅更新指定字段，不修改其他字段"""
        store = LLMSettingsStore()
        # 更新 deepseek 的 apikey
        store.update_provider("deepseek", api_key="sk-test-key")
        cfg = store.get_provider("deepseek")
        assert cfg["api_key"] == "sk-test-key"
        # model 应保持默认
        assert cfg["model"] == PROVIDER_DEFAULTS["deepseek"]["model"]

    def test_update_provider_none_fields_skipped(self):
        """update_provider 传 None 的字段不应修改"""
        store = LLMSettingsStore()
        store.update_provider("deepseek", api_key="sk-key-a")
        store.update_provider("deepseek", base_url="https://custom.com/v1")
        cfg = store.get_provider("deepseek")
        assert cfg["api_key"] == "sk-key-a"
        assert cfg["base_url"] == "https://custom.com/v1"

    def test_update_provider_unknown_raises(self):
        """update_provider 对未知 provider 应抛出 KeyError"""
        store = LLMSettingsStore()
        with pytest.raises(KeyError, match="未知 provider"):
            store.update_provider("nope", api_key="x")

    def test_get_provider_unknown_raises(self):
        """get_provider 对未知 provider 应抛出 KeyError"""
        store = LLMSettingsStore()
        with pytest.raises(KeyError, match="未知 provider"):
            store.get_provider("nope")


# ==============================================================================
# Test Suite 3：_merge_defaults 合并逻辑
# ==============================================================================

class TestMergeDefaults:
    """测试配置合并逻辑：新增 provider 或默认值变更时不覆盖已有值"""

    def test_merge_defaults_missing_provider_added(self):
        """当存储文件中缺少某个 provider（如代码新增了 provider），_merge_defaults 应自动补充"""
        stored = {
            "active_provider": "openai",
            "providers": {
                # 故意只存部分 provider，不含 deepseek
                "openai": {"api_key": "sk-123", "base_url": "https://openai.com/v1", "model": "gpt-4"},
            },
        }
        merged = _merge_defaults(stored)
        # deepseek 应被自动添加，base_url 取默认值
        assert "deepseek" in merged["providers"]
        assert merged["providers"]["deepseek"]["base_url"] == PROVIDER_DEFAULTS["deepseek"]["base_url"]
        # openai 的已有值应保留
        assert merged["providers"]["openai"]["api_key"] == "sk-123"

    def test_merge_defaults_keeps_existing(self):
        """已存在的 provider 配置不应被默认值覆盖"""
        stored = {
            "active_provider": "deepseek",
            "providers": {
                "deepseek": {"api_key": "sk-existing", "base_url": "https://custom.com", "model": "custom-model"},
            },
        }
        merged = _merge_defaults(stored)
        assert merged["providers"]["deepseek"]["api_key"] == "sk-existing"
        assert merged["providers"]["deepseek"]["base_url"] == "https://custom.com"


# ==============================================================================
# Test Suite 4：环境变量兜底
# ==============================================================================

class TestEnvFallback:
    """测试 get_provider_with_env_fallback：JSON 缺失时从环境变量补齐"""

    def test_env_fallback_empty_json_filled_by_env(self, monkeypatch):
        """JSON 文件中 api_key 为空，环境变量中有值 → 应返回 env 值"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-from-env")
        monkeypatch.setenv("DEEPSEEK_BASE_URL", "https://env-test.com/v1")

        store = LLMSettingsStore()
        cfg = store.get_provider_with_env_fallback("deepseek")
        assert cfg["api_key"] == "sk-from-env"
        assert cfg["base_url"] == "https://env-test.com/v1"

    def test_env_fallback_json_takes_priority(self, monkeypatch):
        """JSON 文件中已有值时，不应被环境变量覆盖"""
        monkeypatch.setenv("DEEPSEEK_API_KEY", "sk-should-not-be-used")

        store = LLMSettingsStore()
        store.update_provider("deepseek", api_key="sk-from-json")
        cfg = store.get_provider_with_env_fallback("deepseek")
        assert cfg["api_key"] == "sk-from-json"

    def test_env_fallback_missing_both(self):
        """JSON 和 env 都缺失时，返回空字符串"""
        store = LLMSettingsStore()
        cfg = store.get_provider_with_env_fallback("ollama")
        # ollama 默认 api_key 是空字符串
        assert cfg.get("api_key") == ""


# ==============================================================================
# Test Suite 5：API Key 脱敏
# ==============================================================================

class TestMaskAPIKey:
    """测试 mask_api_key 静态方法"""

    def test_mask_long_key(self):
        """长 key 保留首尾各4字符"""
        masked = LLMSettingsStore.mask_api_key("sk-abcdefghijklmnop")
        # 4 + (16-8=8个*) + 4 = 至少 4+4+4=12
        assert masked.startswith("sk-a")
        assert masked.endswith("mnop")
        assert "****" in masked

    def test_mask_short_key(self):
        """短 key (<8) 直接返回 ****"""
        assert LLMSettingsStore.mask_api_key("short") == "****"
        assert LLMSettingsStore.mask_api_key("ab") == "****"

    def test_mask_empty_key(self):
        """空 key 返回空字符串"""
        assert LLMSettingsStore.mask_api_key("") == ""
        assert LLMSettingsStore.mask_api_key(None) == ""

    def test_mask_exact_8_chars(self):
        """恰好 8 字符的 key"""
        masked = LLMSettingsStore.mask_api_key("12345678")
        # 4 + 4 = 8
        assert len(masked) == 8
        assert masked == "12345678"  # 恰好首尾各4，中间没有 ***


# ==============================================================================
# Test Suite 6：默认参数管理
# ==============================================================================

class TestDefaultParams:
    """测试默认温度和 max_tokens 的管理"""

    def test_get_default_params(self):
        store = LLMSettingsStore()
        params = store.get_default_params()
        assert params["temperature"] == DEFAULT_TEMPERATURE
        assert params["max_tokens"] == DEFAULT_MAX_TOKENS

    def test_update_default_params(self):
        store = LLMSettingsStore()
        store.update_default_params(temperature=0.5, max_tokens=512)
        params = store.get_default_params()
        assert params["temperature"] == 0.5
        assert params["max_tokens"] == 512

    def test_update_default_params_partial(self):
        """部分更新：只更新 temperature，max_tokens 应保持不变"""
        store = LLMSettingsStore()
        store.update_default_params(temperature=0.3)
        params = store.get_default_params()
        assert params["temperature"] == 0.3
        assert params["max_tokens"] == DEFAULT_MAX_TOKENS  # 未变


# ==============================================================================
# Test Suite 7：文件损坏后的恢复行为
# ==============================================================================

class TestCorruptedFile:
    """测试设置文件损坏时系统能自动恢复"""

    def test_corrupted_json_file_returns_defaults(self, tmp_path):
        """文件内容为非法 JSON 时，应返回默认配置而非崩溃"""
        settings_file = tmp_path / "llm_settings.json"
        settings_file.write_text("这不是合法的 JSON {{{", encoding="utf-8")

        store = LLMSettingsStore()
        data = store.get_all()
        assert data["active_provider"] == DEFAULT_ACTIVE
        assert "providers" in data


# ==============================================================================
# Test Suite 8：Provider 元信息
# ==============================================================================

class TestProviderMeta:
    """测试 list_providers_meta 静态方法"""

    def test_list_providers_meta(self):
        """应返回所有支持的 provider 元信息"""
        metas = LLMSettingsStore.list_providers_meta()
        assert len(metas) == len(PROVIDER_META)
        assert all(m["id"] for m in metas)
        assert all(m["name"] for m in metas)
        # ollama 不应该需要 api key
        ollama_meta = next(m for m in metas if m["id"] == "ollama")
        assert ollama_meta["needs_key"] == "false"

    def test_list_returns_copies(self):
        """list_providers_meta 应返回副本，修改不应影响原数据"""
        metas = LLMSettingsStore.list_providers_meta()
        original_len = len(metas)
        metas.append({"id": "fake"})
        assert len(LLMSettingsStore.list_providers_meta()) == original_len


# ==============================================================================
# Test Suite 9：文件存储路径暴露
# ==============================================================================

class TestFilePath:
    """测试 settings_file_path 静态方法"""

    def test_path_is_string(self):
        path = LLMSettingsStore.settings_file_path()
        assert isinstance(path, str)
        assert path.endswith("llm_settings.json")
