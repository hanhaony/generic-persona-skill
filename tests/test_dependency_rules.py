"""依赖方向与隔离规则的静态检查（INTERFACES §13.2、DECISIONS D-022/D-054/D-057/D-059）。

这些断言把"写在文档里的规矩"变成**会被测试拦住**的规矩：
  F-2  Evidence 不得自行启动研究循环（签名里不许有 search / collect / query_plan）
  F-3  Memory 不得写证据（签名里不许有 add_evidence）
  F-7  CLI 不得绕过 Orchestrator 直接驱动业务模块
  F-8  Storage 不得依赖 Provider / 业务模块 / 外部服务
  F-12 Provider 不得依赖任何业务模块
  另外：任何模块都不许 import 具体厂商 SDK（D-054）
"""

import inspect
import pathlib
import unittest

import persona.modules.dialogue as dialogue_mod
import persona.modules.evidence as evidence_mod
import persona.modules.memory as memory_mod
from persona.providers.base import Provider

PACKAGE_ROOT = pathlib.Path(__file__).resolve().parents[1] / "persona"

FORBIDDEN_IMPORTS = ("openai", "httpx", "requests", "anthropic", "boto3")


def _source_of(directory: str) -> str:
    texts = []
    for path in sorted((PACKAGE_ROOT / directory).rglob("*.py")):
        texts.append(path.read_text(encoding="utf-8"))
    return "\n".join(texts)


def _public_methods(protocol_cls) -> set[str]:
    """列出协议里声明的公开方法（跳过 `__init__` 等内部成员）。"""
    return {
        name
        for name, _ in inspect.getmembers(protocol_cls, inspect.isfunction)
        if not name.startswith("_")
    }


class TestPortSignatures(unittest.TestCase):
    def test_evidence_port_has_no_research_methods(self):
        """F-2：Evidence 不许自己启动研究循环。"""
        names = _public_methods(evidence_mod.EvidencePort)
        self.assertNotIn("search", names)
        self.assertNotIn("collect", names)
        self.assertNotIn("query_plan", names)
        self.assertIn("add_evidence", names)

    def test_memory_port_has_no_evidence_writing(self):
        """F-3 / INV-3：记忆永远不能升级成证据。"""
        names = _public_methods(memory_mod.MemoryPort)
        self.assertNotIn("add_evidence", names)
        self.assertNotIn("record_inference", names)

    def test_dialogue_gets_read_only_ports_only(self):
        """D-057：Dialogue 只拿到只读端口 + 模拟记忆写入端口。"""
        evidence_reader = _public_methods(evidence_mod.EvidenceReader)
        self.assertTrue({"query_evidence", "get_evidence_tier"} <= evidence_reader)
        self.assertNotIn("add_evidence", evidence_reader)

        memory_writer = _public_methods(dialogue_mod.MemoryWriterPort)
        self.assertEqual(memory_writer, {"write_simulated"})

    def test_provider_interface_is_complete(self):
        self.assertEqual(
            _public_methods(Provider),
            {"capabilities", "chat", "search", "usage", "health"},
        )


class TestImportRules(unittest.TestCase):
    def test_storage_does_not_depend_on_providers_or_modules(self):
        """F-8：Storage 不做业务判断，也不碰外部服务。"""
        text = _source_of("storage")
        self.assertNotIn("from ..providers", text)
        self.assertNotIn("from ..modules", text)
        self.assertIn("import sqlite3", text)  # 标准库 sqlite3 允许（D-041）

    def test_providers_do_not_depend_on_modules(self):
        """F-12：Provider 不理解业务。"""
        text = _source_of("providers")
        self.assertNotIn("from ..modules", text)
        self.assertNotIn("from ..orchestrator", text)

    def test_cli_does_not_drive_business_modules(self):
        """F-7 / D-059：CLI 必须经 Orchestrator。"""
        text = _source_of("cli")
        self.assertNotIn("from ..modules", text)
        self.assertNotIn("import persona.modules", text)
        self.assertNotIn("from persona.modules", text)

    def test_modules_do_not_import_orchestrator(self):
        """F-1：下层永不回调上层。"""
        text = _source_of("modules")
        self.assertNotIn("from ..orchestrator", text)
        self.assertNotIn("from ..cli", text)

    def test_no_vendor_sdk_anywhere(self):
        """D-054：Provider 是唯一外部出口。"""
        offenders = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            for bad in FORBIDDEN_IMPORTS:
                if f"import {bad}" in text or f"from {bad}" in text:
                    offenders.append((str(path.relative_to(PACKAGE_ROOT)), bad))
        self.assertEqual(offenders, [])

    def test_no_api_key_literals(self):
        import re

        offenders = []
        # 精确匹配"字面量密钥赋值"（如 `api_key = "sk-..."` / `api_key='...'`），
        # 不误伤：
        #   - 环境变量名常量定义（`ENV_PROVIDER_API_KEY = "PERSONA_..."`，
        #     其中 "api_key" 前面是下划线，构成 env_provider_api_key 整体标识符）；
        #   - 合法形参传递（`TavilyProvider(api_key=key)`）；
        #   - 实例属性赋值（`self.api_key = api_key or ""`，= 后无引号）；
        #   - 文档/注释里对 Bearer **方案名**的说明（`Authorization: Bearer <api_key>`、
        #     `f"Bearer {self.api_key}"`）—— 那是写法说明，不是硬编码密钥。
        # 负向后查 `(?<![A-Za-z0-9_])` 确保 "api_key" 不是更长标识符的一部分。
        # bearer 之后要求接一段"长得像真密钥"的长 token（≥16 位），
        # 才能判定为硬编码密钥；占位符 `<api_key>` / `{self.api_key}` 不匹配。
        patterns = (
            re.compile(r"(?<![A-Za-z0-9_])api_key\s*=\s*['\"]"),
            re.compile(r"sk-[A-Za-z0-9]{6,}"),          # 真实密钥前缀后接足够字符
            re.compile(r"authorization\s*:\s*bearer\s+[A-Za-z0-9_\-]{16,}",
                       re.IGNORECASE),
        )
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8")
            lowered = text.lower()
            for pat in patterns:
                if pat.search(lowered):
                    offenders.append((str(path.relative_to(PACKAGE_ROOT)), pat.pattern))
        self.assertEqual(offenders, [])


class TestNoPersonSpecificLogic(unittest.TestCase):
    """AC-16 / D-001：代码与配置里不得出现特定真实人物的专属设定。"""

    SUSPICIOUS = (
        "特朗普", "拜登", "马斯克", "马云", "鲁迅", "爱因斯坦",
        "trump", "biden", "musk", "obama",
    )

    def test_source_has_no_real_person_names(self):
        offenders = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            text = path.read_text(encoding="utf-8").lower()
            for name in self.SUSPICIOUS:
                if name in text:
                    offenders.append((str(path.relative_to(PACKAGE_ROOT)), name))
        self.assertEqual(offenders, [])


if __name__ == "__main__":
    unittest.main()
