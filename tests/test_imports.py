"""模块导入测试：包结构必须完整可导入（这是"骨架立得住"的最低要求）。"""

import importlib
import unittest

MODULES = [
    "persona",
    "persona.core",
    "persona.core.enums",
    "persona.core.ids",
    "persona.core.temporal",
    "persona.core.errors",
    "persona.core.result",
    "persona.core.context",
    "persona.core.types",
    "persona.storage",
    "persona.storage.schema",
    "persona.storage.sqlite",
    "persona.providers",
    "persona.providers.base",
    "persona.providers.mock",
    "persona.providers.registry",
    "persona.orchestrator",
    "persona.orchestrator.state",
    "persona.orchestrator.checkpoint",
    "persona.modules",
    "persona.modules.research",
    "persona.modules.evidence",
    "persona.modules.persona",
    "persona.modules.relationship",
    "persona.modules.dialogue",
    "persona.modules.memory",
    "persona.modules.evaluation",
    "persona.cli",
    "persona.cli.main",
]


class TestImports(unittest.TestCase):
    def test_all_modules_importable(self):
        for name in MODULES:
            with self.subTest(module=name):
                importlib.import_module(name)

    def test_version_and_stage_declared(self):
        import persona

        self.assertTrue(persona.__version__)
        # 阶段号随开发推进而变，这里只要求"声明了阶段"，不钉死具体编号
        self.assertTrue(persona.STAGE.startswith("P"), persona.STAGE)


if __name__ == "__main__":
    unittest.main()
