"""配置管理的测试（P2.2）。

覆盖 `docs/CONFIG.md` 与 DECISIONS §2.12 的规矩：
    D-077 统一入口（全项目只有 config 包读环境变量）
    D-078 非敏感 vs 敏感分离
    D-079 不引入 dotenv 依赖，自带极简解析；.env 不写密钥
    D-080 四级优先级
    D-081 错误信息不含密钥值
    D-082 未知项只告警，非法值才失败

本文件**全程离线**：不访问外部服务，不使用真实密钥。
"""

import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

from persona.config import (
    CONFIG_FIELDS,
    ENV_PROVIDER_API_KEY,
    ENV_SEARCH_API_KEY,
    AppSettings,
    LogLevel,
    RunMode,
    Secrets,
    default_settings,
    describe_fields,
    load_settings,
    parse_dotenv,
    read_dotenv,
    test_settings,
    validate_settings,
)
from persona.config.loader import (
    WARNING_SECRET_IN_DOTENV,
    WARNING_UNKNOWN_KEY,
)
from persona.core.errors import ErrorCode
from persona.core.result import is_err, is_ok, unwrap

# 一个**假**的密钥值，仅用于测试"不会泄露"，不是任何真实凭据
FAKE_SECRET = "x" * 24


def _ok(result):
    return unwrap(result)


class TestDotenvParsing(unittest.TestCase):
    """D-079：自带极简解析，不引入 python-dotenv。"""

    def test_basic_key_value(self):
        parsed = parse_dotenv("PERSONA_MODE=test\nPERSONA_LOG_LEVEL=DEBUG\n")
        self.assertEqual(parsed["PERSONA_MODE"], "test")
        self.assertEqual(parsed["PERSONA_LOG_LEVEL"], "DEBUG")

    def test_comments_and_blank_lines_ignored(self):
        parsed = parse_dotenv("# 这是注释\n\n   \nPERSONA_MODE=prod  # 行尾注释不支持\n")
        self.assertEqual(parsed["PERSONA_MODE"], "prod  # 行尾注释不支持")

    def test_quotes_stripped(self):
        parsed = parse_dotenv('A="hello"\nB=\'world\'\n')
        self.assertEqual(parsed["A"], "hello")
        self.assertEqual(parsed["B"], "world")

    def test_export_prefix_supported(self):
        parsed = parse_dotenv("export PERSONA_MODE=test\n")
        self.assertEqual(parsed["PERSONA_MODE"], "test")

    def test_missing_file_returns_empty(self):
        self.assertEqual(read_dotenv(Path(tempfile.gettempdir()) / "no-such-file.env"), {})

    def test_real_file_read(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text("PERSONA_MODE=prod\n", encoding="utf-8")
            self.assertEqual(read_dotenv(path)["PERSONA_MODE"], "prod")


class TestDefaultSettings(unittest.TestCase):
    def test_defaults_are_offline_and_keyless(self):
        settings = default_settings()
        self.assertEqual(settings.mode, RunMode.DEV)
        self.assertEqual(settings.log_level, LogLevel.INFO)
        self.assertTrue(settings.offline_only)          # 默认离线 = 默认不花钱
        self.assertIsNone(settings.secrets.provider_api_key)
        self.assertIsNone(settings.secrets.search_api_key)

    def test_default_settings_pass_validation(self):
        self.assertEqual(validate_settings(default_settings()), [])

    def test_default_database_path_under_data_dir(self):
        settings = default_settings()
        self.assertEqual(settings.database_path("/tmp/root"), Path("/tmp/root/var/persona.db"))

    def test_absolute_data_dir_is_respected(self):
        settings = replace(default_settings(), data_dir="/abs/dir")
        self.assertEqual(settings.database_path("/ignored"), Path("/abs/dir/persona.db"))


class TestEnvOverride(unittest.TestCase):
    def test_env_overrides_defaults(self):
        result = load_settings(
            env={"PERSONA_MODE": "prod", "PERSONA_BUDGET_MAX_REQUESTS": "42"},
            use_dotenv=False,
        )
        self.assertTrue(is_ok(result))
        settings = _ok(result)
        self.assertEqual(settings.mode, RunMode.PROD)
        self.assertEqual(settings.budget.max_requests, 42)
        self.assertEqual(settings.sources["mode"], "env")
        self.assertEqual(settings.sources["budget.max_requests"], "env")

    def test_untouched_fields_stay_default(self):
        settings = _ok(load_settings(env={"PERSONA_MODE": "test"}, use_dotenv=False))
        self.assertEqual(settings.sources["log_level"], "default")
        self.assertEqual(settings.log_level, LogLevel.INFO)

    def test_env_bool_parsing(self):
        settings = _ok(load_settings(use_dotenv=False, env={
            "PERSONA_OFFLINE_ONLY": "false",
            ENV_PROVIDER_API_KEY: FAKE_SECRET,     # 假值，仅用于走通校验
        }))
        self.assertFalse(settings.offline_only)
        # 不给密钥就关离线 → 校验必然报错（见 TestInvalidConfig.test_online_without_key_fails）
        lonely = _ok(load_settings(use_dotenv=False, env={"PERSONA_OFFLINE_ONLY": "true"}))
        self.assertEqual(validate_settings(lonely), [])


class TestPriority(unittest.TestCase):
    """D-080：默认值 < .env < 环境变量 < overrides。"""

    def _write_env(self, tmp: str, text: str) -> Path:
        path = Path(tmp) / ".env"
        path.write_text(text, encoding="utf-8")
        return path

    def test_dotenv_beats_default(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_env(tmp, "PERSONA_MODE=prod\n")
            settings = _ok(load_settings(env={}, dotenv_path=path))
            self.assertEqual(settings.mode, RunMode.PROD)
            self.assertEqual(settings.sources["mode"], "dotenv")

    def test_env_beats_dotenv(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_env(tmp, "PERSONA_MODE=prod\n")
            settings = _ok(load_settings(env={"PERSONA_MODE": "test"}, dotenv_path=path))
            self.assertEqual(settings.mode, RunMode.TEST)
            self.assertEqual(settings.sources["mode"], "env")

    def test_overrides_beat_env(self):
        settings = _ok(load_settings(
            env={"PERSONA_MODE": "prod"},
            use_dotenv=False,
            overrides={"PERSONA_MODE": "test"},
        ))
        self.assertEqual(settings.mode, RunMode.TEST)
        self.assertEqual(settings.sources["mode"], "explicit")

    def test_use_dotenv_false_skips_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = self._write_env(tmp, "PERSONA_MODE=prod\n")
            settings = _ok(load_settings(env={}, dotenv_path=path, use_dotenv=False))
            self.assertEqual(settings.mode, RunMode.DEV)   # 回到默认值


class TestInvalidConfig(unittest.TestCase):
    """非法值必须报错，且**错误信息里不含密钥值**。"""

    def _err_of(self, **kwargs):
        result = load_settings(use_dotenv=False, **kwargs)
        self.assertTrue(is_err(result), "本该失败却成功了")
        from persona.core.result import error_of

        return error_of(result)

    def test_bad_enum_value(self):
        error = self._err_of(env={"PERSONA_MODE": "super-mode"})
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)
        self.assertIn("PERSONA_MODE", error.message)

    def test_non_integer(self):
        error = self._err_of(env={"PERSONA_BUDGET_MAX_REQUESTS": "abc"})
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)

    def test_out_of_range(self):
        error = self._err_of(env={"PERSONA_BUDGET_MAX_REQUESTS": "0"})
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)
        self.assertIn("下限", error.message)

    def test_non_boolean(self):
        error = self._err_of(env={"PERSONA_OFFLINE_ONLY": "maybe"})
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)

    def test_empty_required_string(self):
        error = self._err_of(env={"PERSONA_DATA_DIR": "   "})
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)

    def test_backoff_max_below_base(self):
        error = self._err_of(env={
            "PERSONA_RETRY_BACKOFF_BASE_MS": "5000",
            "PERSONA_RETRY_BACKOFF_MAX_MS": "100",
        })
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)
        self.assertIn("退避上限", error.message)

    def test_config_error_is_not_retryable(self):
        error = self._err_of(env={"PERSONA_MODE": "nope"})
        self.assertFalse(error.retryable)
        self.assertFalse(error.recoverable)

    def test_online_without_key_fails(self):
        """允许真实调用却没给密钥 → 失败（避免"静默花真钱"）。"""
        error = self._err_of(env={"PERSONA_OFFLINE_ONLY": "false"})
        self.assertEqual(error.code, ErrorCode.VALIDATION_REQUIRED_FIELD)
        self.assertIn(ENV_PROVIDER_API_KEY, error.message)

    def test_short_key_is_rejected(self):
        error = self._err_of(env={
            "PERSONA_OFFLINE_ONLY": "false",
            ENV_PROVIDER_API_KEY: "123",
        })
        self.assertEqual(error.code, ErrorCode.VALIDATION_CONFIG_INVALID)


class TestSecretSafety(unittest.TestCase):
    """D-078 / D-081：密钥绝不出现在 repr / 日志 / 错误信息里。"""

    def test_secrets_repr_hides_value(self):
        secrets = Secrets(FAKE_SECRET)  # 位置参数：provider 的密钥
        self.assertNotIn(FAKE_SECRET, repr(secrets))
        self.assertNotIn(FAKE_SECRET, str(secrets))
        self.assertIn("已设置", repr(secrets))

    def test_settings_repr_hides_value(self):
        settings = replace(default_settings(), secrets=Secrets(FAKE_SECRET))
        self.assertNotIn(FAKE_SECRET, repr(settings))
        self.assertNotIn(FAKE_SECRET, str(settings))
        self.assertNotIn(FAKE_SECRET, str(settings.to_public_dict()))

    def test_error_message_hides_value(self):
        result = load_settings(use_dotenv=False, env={
            "PERSONA_OFFLINE_ONLY": "false",
            ENV_PROVIDER_API_KEY: "short",
        })
        self.assertTrue(is_err(result))
        from persona.core.result import error_of

        message = error_of(result).message
        self.assertNotIn("short", message)
        self.assertIn(ENV_PROVIDER_API_KEY, message)

    def test_secret_from_env_only(self):
        settings = _ok(load_settings(use_dotenv=False, env={
            "PERSONA_OFFLINE_ONLY": "false",
            ENV_PROVIDER_API_KEY: FAKE_SECRET,
        }))
        self.assertTrue(settings.secrets.provider_api_key)
        self.assertEqual(settings.secrets.status()[ENV_PROVIDER_API_KEY], True)

    def test_secret_in_dotenv_is_ignored_with_warning(self):
        """把密钥写进 .env 不算数——程序忽略它、告警，并且真的没读到。"""
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / ".env"
            path.write_text(f"{ENV_PROVIDER_API_KEY}={FAKE_SECRET}\n", encoding="utf-8")
            result = load_settings(env={}, dotenv_path=path)
            self.assertTrue(is_ok(result), "忽略密钥不应导致加载失败")
            settings = _ok(result)
            self.assertIsNone(settings.secrets.provider_api_key)
            codes = [note.code for note in result.warnings]
            self.assertIn(WARNING_SECRET_IN_DOTENV, codes)
            self.assertNotIn(FAKE_SECRET, " ".join(note.message for note in result.warnings))

    def test_secret_in_overrides_is_ignored(self):
        result = load_settings(
            use_dotenv=False,
            env={"PERSONA_OFFLINE_ONLY": "false"},
            overrides={ENV_PROVIDER_API_KEY: FAKE_SECRET},
        )
        self.assertTrue(is_err(result), "overrides 里的密钥必须被忽略")

    def test_env_example_has_no_real_secret(self):
        path = Path(__file__).resolve().parents[1] / ".env.example"
        self.assertTrue(path.is_file(), ".env.example 应该存在")
        text = path.read_text(encoding="utf-8")
        parsed = parse_dotenv(text)
        for key, value in parsed.items():
            self.assertNotIn(key, (ENV_PROVIDER_API_KEY, ENV_SEARCH_API_KEY),
                             ".env.example 不得写入任何密钥（哪怕是假的）")

    def test_no_getenv_outside_config_package(self):
        """D-077：除了 persona/config，其它包不许自己读环境变量。

        用 AST 扫描真实代码节点（属性访问/名称引用），忽略文档字符串与注释，
        因此 tavily.py 等模块里"本类从不读取 os.getenv"之类的说明文字不会误报。
        """
        import ast
        import pathlib

        package_root = pathlib.Path(__file__).resolve().parents[1] / "persona"
        offenders = []
        for file_path in sorted(package_root.rglob("*.py")):
            if file_path.parent.name == "config":
                continue
            text = file_path.read_text(encoding="utf-8")
            try:
                tree = ast.parse(text)
            except SyntaxError:
                continue
            for node in ast.walk(tree):
                # os.getenv(...) / os.environ[...] / os.environ.get(...)
                if isinstance(node, ast.Attribute) and node.attr in ("getenv", "environ"):
                    offenders.append(str(file_path.relative_to(package_root)))
                    break
                # bare getenv(...) 引用
                if isinstance(node, ast.Name) and node.id == "getenv":
                    offenders.append(str(file_path.relative_to(package_root)))
                    break
        self.assertEqual(offenders, [])


class TestUnknownKeys(unittest.TestCase):
    """D-082：未知项只告警，不失败。"""

    def test_unknown_key_warns_but_loads(self):
        result = load_settings(use_dotenv=False, env={"PERSONA_TYPO_LEVEL": "9"})
        self.assertTrue(is_ok(result))
        codes = [note.code for note in result.warnings]
        self.assertIn(WARNING_UNKNOWN_KEY, codes)

    def test_unrelated_env_is_not_touched(self):
        result = load_settings(use_dotenv=False, env={"PATH": "/usr/bin", "HOME": "/root"})
        self.assertTrue(is_ok(result))
        self.assertEqual(result.warnings, [])


class TestOnlineConfig(unittest.TestCase):
    def test_online_with_valid_key_passes(self):
        result = load_settings(use_dotenv=False, env={
            "PERSONA_OFFLINE_ONLY": "false",
            ENV_PROVIDER_API_KEY: FAKE_SECRET,
        })
        self.assertTrue(is_ok(result))
        self.assertFalse(_ok(result).offline_only)

    def test_search_key_optional_when_offline(self):
        result = load_settings(use_dotenv=False, env={})
        self.assertTrue(is_ok(result))
        self.assertIsNone(_ok(result).secrets.search_api_key)


class TestIntegration(unittest.TestCase):
    """与已有模块的衔接（只验证形状，不做业务）。"""

    def test_retry_limits_convert_to_provider_config(self):
        from persona.providers.base import ProviderConfig

        settings = _ok(load_settings(use_dotenv=False, env={
            "PERSONA_RETRY_MAX_RETRIES": "5",
            "PERSONA_RETRY_TIMEOUT_MS": "1234",
        }))
        config = settings.to_provider_config()
        self.assertIsInstance(config, ProviderConfig)
        self.assertEqual(config.max_retries, 5)
        self.assertEqual(config.per_call_timeout_ms, 1234)

    def test_database_path_feeds_sqlite_storage(self):
        from persona.core.context import new_call_context
        from persona.core.result import is_ok as ok
        from persona.storage.sqlite import SqliteStorage

        with tempfile.TemporaryDirectory() as tmp:
            settings = test_settings(data_dir=str(Path(tmp) / "cfg-db"))
            store = SqliteStorage(settings.ensure_data_dir(tmp))
            ctx = new_call_context(project_id="prj_cfg", actor="test")
            self.assertTrue(ok(store.init_schema(ctx)))
            store.close()
            self.assertTrue((Path(tmp) / "cfg-db" / "persona.db").exists())


class TestTestSettings(unittest.TestCase):
    def test_test_settings_is_offline_and_keyless(self):
        settings = test_settings()
        self.assertEqual(settings.mode, RunMode.TEST)
        self.assertTrue(settings.offline_only)
        self.assertFalse(settings.secrets.all_set())

    def test_overrides_by_path(self):
        settings = test_settings(data_dir="/tmp/x", **{"budget.max_requests": 7})
        self.assertEqual(settings.data_dir, "/tmp/x")
        self.assertEqual(settings.budget.max_requests, 7)

    def test_invalid_override_raises(self):
        with self.assertRaises(AssertionError):
            test_settings(**{"budget.max_requests": -1})


class TestDescribeFields(unittest.TestCase):
    def test_every_field_has_env_key_and_help(self):
        for field in describe_fields():
            self.assertTrue(field["env_key"].startswith("PERSONA_"))
            self.assertTrue(field["path"])
            self.assertTrue(field["help"])

    def test_no_sensitive_field_in_registry(self):
        keys = {field["env_key"] for field in describe_fields()}
        self.assertNotIn(ENV_PROVIDER_API_KEY, keys)
        self.assertNotIn(ENV_SEARCH_API_KEY, keys)

    def test_registry_matches_config_fields(self):
        self.assertEqual(len(describe_fields()), len(CONFIG_FIELDS))


if __name__ == "__main__":
    unittest.main()
