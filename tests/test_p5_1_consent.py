"""P5.1 授权确认（`persona/cli/consent.py`）离线测试。

这一层只有一件事要做对：**记住"人同意过真实调用"，且只记这一件事**。
所以测试的重心不是"能不能读回来"，而是**它到底存了什么**：

1. 文件里**只有三个键**（`version` / `confirmed_at` / `estimated_usd`）——
   不存密钥、不存命令、不存材料、不存人名。这个文件要能被随手打开看一眼
   而不泄露任何东西。
2. 按**数据目录**隔离：换 `--out` 就重新问一次。"在 A 目录同意过"不能替
   B 目录花钱背书。
3. 坏文件不炸：确认文件坏了最坏的结果是"多问一次"，绝不能是"run 崩掉"。
4. 问句里的次数与金额**由调用方传入**（`plan_calls` 是唯一口径），
   这里不许自己算——算两遍就会有两份数字。

全部离线：只碰临时目录里的一个 JSON 文件。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

from persona.cli import consent


def _dir() -> tempfile.TemporaryDirectory:
    return tempfile.TemporaryDirectory(prefix="gps-p51-consent-")


class ConsentFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _dir()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)

    def test_path_lives_in_the_data_directory(self):
        self.assertEqual(consent.consent_path(self.root),
                         self.root / ".persona_consent.json")
        self.assertEqual(consent.CONSENT_FILENAME, ".persona_consent.json")

    def test_missing_file_is_not_confirmed(self):
        self.assertFalse(consent.is_confirmed(self.root))
        self.assertIsNone(consent.load_consent(self.root))

    def test_remember_writes_exactly_three_keys(self):
        consent.remember(self.root, estimated_usd=0.0066)
        payload = json.loads(consent.consent_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(set(payload), {"version", "confirmed_at", "estimated_usd"})
        self.assertEqual(set(payload), set(consent.ALLOWED_KEYS))

    def test_payload_has_no_secret_command_or_name(self):
        """最要紧的一条：确认文件里不能有任何"顺手带进去"的东西。"""
        consent.remember(self.root, estimated_usd=0.25)
        raw = consent.consent_path(self.root).read_text(encoding="utf-8")
        for forbidden in ("sk-", "persona run", "--names", "林黛玉", "api_key",
                          "token", "password", "tavily"):
            self.assertNotIn(forbidden, raw, f"确认文件里不该出现 {forbidden!r}")

    def test_version_is_recorded(self):
        consent.remember(self.root, estimated_usd=0.1)
        payload = consent.load_consent(self.root)
        self.assertEqual(payload["version"], consent.CONSENT_VERSION)

    def test_estimated_usd_is_rounded_to_six_places(self):
        consent.remember(self.root, estimated_usd=0.123456789)
        payload = consent.load_consent(self.root)
        self.assertEqual(payload["estimated_usd"], 0.123457)

    def test_unknown_estimate_is_stored_as_null_not_zero(self):
        """估不出来就存 null：存 0 会被下次读成"这次不要钱"。"""
        consent.remember(self.root, estimated_usd=None)
        payload = consent.load_consent(self.root)
        self.assertIsNone(payload["estimated_usd"])

    def test_confirmed_at_is_an_iso_timestamp(self):
        when = datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
        consent.remember(self.root, estimated_usd=0.01, now=when)
        payload = consent.load_consent(self.root)
        self.assertEqual(payload["confirmed_at"], "2026-05-06T07:08:09Z")

    def test_remember_then_confirmed(self):
        self.assertFalse(consent.is_confirmed(self.root))
        consent.remember(self.root, estimated_usd=0.01)
        self.assertTrue(consent.is_confirmed(self.root))

    def test_no_temporary_file_is_left_behind(self):
        consent.remember(self.root, estimated_usd=0.01)
        leftovers = [p.name for p in self.root.iterdir() if p.name.endswith(".tmp")]
        self.assertEqual(leftovers, [])

    def test_forget_turns_the_question_back_on(self):
        consent.remember(self.root, estimated_usd=0.01)
        self.assertTrue(consent.forget(self.root))
        self.assertFalse(consent.is_confirmed(self.root))
        self.assertFalse(consent.forget(self.root), "文件已经不在了，不该报错")

    def test_remember_creates_the_directory_if_needed(self):
        nested = self.root / "out" / "nested"
        consent.remember(nested, estimated_usd=0.01)
        self.assertTrue(consent.consent_path(nested).exists())


class ConsentIsolationTest(unittest.TestCase):
    def setUp(self) -> None:
        self._outer = _dir()
        self.addCleanup(self._outer.cleanup)
        self.base = Path(self._outer.name)

    def test_each_data_directory_asks_separately(self):
        """换 `--out` 就重新问一次（用户拍板：按目录隔离）。"""
        first = self.base / "run-a"
        second = self.base / "run-b"
        consent.remember(first, estimated_usd=0.01)
        self.assertTrue(consent.is_confirmed(first))
        self.assertFalse(consent.is_confirmed(second))


class ConsentRobustnessTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = _dir()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name)
        self.path = consent.consent_path(self.root)

    def _write(self, text: str) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        self.path.write_text(text, encoding="utf-8")

    def test_broken_json_means_ask_again(self):
        self._write("{not json")
        self.assertIsNone(consent.load_consent(self.root))
        self.assertFalse(consent.is_confirmed(self.root))

    def test_non_object_json_means_ask_again(self):
        self._write("[1, 2, 3]")
        self.assertIsNone(consent.load_consent(self.root))

    def test_unknown_version_means_ask_again(self):
        self._write(json.dumps({"version": 99, "confirmed_at": "2026-01-01T00:00:00Z"}))
        self.assertIsNone(consent.load_consent(self.root))

    def test_empty_confirmed_at_means_ask_again(self):
        self._write(json.dumps({"version": 1, "confirmed_at": "   "}))
        self.assertIsNone(consent.load_consent(self.root))

    def test_unexpected_keys_are_dropped_on_read(self):
        """读到多余字段就丢掉，不往调用方传（防"顺手存了点别的"）。"""
        self._write(json.dumps({
            "version": 1, "confirmed_at": "2026-01-01T00:00:00Z",
            "estimated_usd": 0.5, "api_key": "sk-should-not-leak"}))
        payload = consent.load_consent(self.root)
        self.assertEqual(set(payload), set(consent.ALLOWED_KEYS))


class ConsentQuestionTest(unittest.TestCase):
    def test_question_states_searches_model_calls_and_cost(self):
        text = consent.build_question(searches=2, model_calls=2, estimated_usd=0.0075,
                                      budget_usd=0.05)
        self.assertIn("将要发生 2 次检索 + 2 次模型调用", text)
        self.assertIn("预估 0.007 USD", text)
        self.assertIn("预算软上限 0.05 USD", text)
        self.assertIn("[y/N]", text)

    def test_question_without_budget_omits_the_cap(self):
        text = consent.build_question(searches=1, model_calls=1, estimated_usd=0.0033,
                                      budget_usd=None)
        self.assertNotIn("预算软上限", text)
        self.assertNotIn("单任务上限", text)
        self.assertIn("预估 0.003 USD", text)

    def test_unknown_cost_is_stated_not_zeroed(self):
        text = consent.build_question(searches=1, model_calls=1, estimated_usd=None,
                                      budget_usd=0.25)
        self.assertIn("预估费用未知", text)
        self.assertNotIn("0.000 USD", text)

    def test_question_defaults_to_no_go(self):
        """问句默认 N：直接回车不花钱。"""
        text = consent.build_question(searches=1, model_calls=1, estimated_usd=0.01,
                                      budget_usd=None)
        self.assertTrue(text.endswith("继续？[y/N]"))

    def test_previous_note_mentions_when_and_how_to_reset(self):
        with _dir() as tmp:
            when = datetime(2026, 5, 6, 7, 8, 9, tzinfo=timezone.utc)
            consent.remember(tmp, estimated_usd=0.01, now=when)
            note = consent.previous_note(tmp)
        self.assertIn("2026-05-06T07:08:09Z", note)
        self.assertIn("不再询问", note)
        self.assertIn(".persona_consent.json", note)
        self.assertIn("删掉", note)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
