"""P5.1 CLI 六条命令（`persona/cli/main.py`）离线测试。

这一层是用户真正的接触面，所以测的不是"函数被调用了"，而是**用户会看到什么、
退出码是什么、有没有留下不该留的东西**：

1. 只读命令（`show` / `show-relationship` / `evidence` / `export` / `status`）
   **绝不写库**：库不存在就报错，不新建（§12 纪律 4）。
2. `run --dry` **零写入零外呼**：跑完数据库文件不存在、也不产生确认文件
   （§10.1 关键断言 ④）。
3. `run` 的授权是**一次人**：TTY 问一次并记住；非 TTY 必须显式 `--yes`；
   回答 `n` 什么都不发生；`--yes` 与 `--dry` 都**不写**确认文件
   （§3.1.1 六分支）。
4. 帮助文本里的两句话是产品承诺，不许被"精简"掉（§10.1 关键断言 ⑧）。

`run` 的成功路径全部**打桩**：`build_business_stack` / `plan_calls` /
`register_targets` 换成假货，所以这里一次网络请求、一分钱都不会发生。
真实端到端只在 `docs/P5_1_REAL_RUN.md` 那次受控真跑里做一次。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

from persona.cli import consent
from persona.cli.main import EXIT_OK, main
from persona.core.enums import Cardinality, EvidenceDimension
from persona.core.result import Ok, unwrap
from persona.modules.relationship import PerspectiveBlock, RelationshipDraft
from tests.test_p5_1_readmodels import PROJECT, P51Fixture


def _run(argv: list[str]) -> tuple[int, str]:
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main(argv)
    return code, buf.getvalue()


# ==========================================================================
# 只读命令：一个真实的临时库，五个命令对着它跑
# ==========================================================================


class ReadCommandsTest(unittest.TestCase):
    """夹具库里有：阿灯（有档案、2 条证据同源）、老周（无档案）、两人关系。"""

    @classmethod
    def setUpClass(cls) -> None:
        cls._tmp = tempfile.mkdtemp(prefix="gps-p51-cli-")
        fx = P51Fixture(cls._tmp)
        hero = fx.entity("阿灯", works=("雾港灯会",), adaptation="2020 动画改编版")
        other = fx.entity("老周", works=("雾港灯会",), adaptation="2020 动画改编版")
        lonely = fx.entity("无档案的人", works=("雾港灯会",), adaptation="2020 动画改编版")
        s1 = fx.source("中青报访谈", url="https://example.test/a")
        s2 = fx.source("图书馆档案", url="https://example.test/b")

        e1 = fx.evidence_of(s1, "他话少但看得准", subjects=[hero],
                            trait_tag="reserved", quote="我没什么可说的。",
                            locator={"field": "body", "start": 3, "end": 18})
        e2 = fx.evidence_of(s1, "他说话留三分", subjects=[hero], trait_tag="reserved")
        e3 = fx.evidence_of(s2, "某次当众发火", subjects=[hero],
                            dimension=EvidenceDimension.EXPERIENCE)
        fx.evidence_of(s2, "设定事实一", subjects=[hero],
                       dimension=EvidenceDimension.CANON_FACT)
        fx.profile(hero, traits=[fx.item("t1", "寡言但观察力敏锐", [e1, e2])])

        ref = fx.evidence_of(s1, "访谈里阿灯多次提到老周靠得住", subjects=[hero, other],
                             dimension=EvidenceDimension.RELATION)
        draft = RelationshipDraft(
            participant_entity_ids=[hero, other],
            cardinality=Cardinality.PAIR,
            perspectives={
                hero: PerspectiveBlock(entity_id=hero, view_of_targets={
                    other: [fx.item("v_ab", "阿灯觉得老周靠得住", [ref])]}),
                other: PerspectiveBlock(entity_id=other, view_of_targets={
                    hero: [fx.item("v_ba", "老周觉得阿灯肯吃苦", [ref])]}),
            },
            applicable_variant_ids=[hero, other],
        )
        unwrap(fx.relationship_service.create_relationship(fx.ctx, draft))
        # 只关连接、**不删目录**：`P51Fixture.close()` 会把库删掉，而这一组测试
        # 要反复对着同一个库跑五条只读命令。
        fx.storage.close()

        cls.path = str(Path(cls._tmp) / "persona.db")
        cls.hero, cls.other, cls.lonely = hero, other, lonely
        cls.e1, cls.e3 = e1, e3

    @classmethod
    def tearDownClass(cls) -> None:
        import shutil
        shutil.rmtree(cls._tmp, ignore_errors=True)

    def _args(self, *rest: str) -> list[str]:
        return [*rest, "--db", self.path, "--project", PROJECT, "--no-dotenv"]

    # ---------------------------------------------------------------- show

    def test_show_prints_the_six_sections(self):
        code, out = _run(self._args("show", "阿灯"))
        self.assertEqual(code, EXIT_OK)
        for title in ("稳定性格", "习惯", "说话方式", "单次行为", "背景", "经历"):
            self.assertIn(title, out)
        self.assertIn("《雾港灯会》 · 改编版本：2020 动画改编版", out)

    def test_show_unknown_name_explains_what_to_run(self):
        code, out = _run(self._args("show", "查无此人"))
        self.assertEqual(code, 1)
        self.assertIn("没有找到「查无此人」", out)
        self.assertIn('persona run --names "查无此人"', out)

    def test_show_without_profile_exits_non_zero_but_still_explains(self):
        code, out = _run(self._args("show", "无档案的人"))
        self.assertEqual(code, 1)
        self.assertIn("没有档案", out)

    def test_show_does_not_create_a_database(self):
        missing = str(Path(self._tmp) / "nowhere" / "persona.db")
        code, out = _run(["show", "阿灯", "--db", missing, "--project", PROJECT,
                          "--no-dotenv"])
        self.assertEqual(code, 1)
        self.assertIn("找不到数据库", out)
        self.assertIn("只读命令**不会**新建数据库", out)
        self.assertFalse(Path(missing).exists(), "只读命令不许建库")

    def test_show_is_read_only(self):
        """跑十次 `show`，库文件的字节数与修改时间都不该变。"""
        before = Path(self.path).stat()
        for _ in range(3):
            _run(self._args("show", "阿灯"))
        after = Path(self.path).stat()
        self.assertEqual(before.st_size, after.st_size)
        self.assertEqual(before.st_mtime_ns, after.st_mtime_ns)

    # ---------------------------------------------------- show-relationship

    def test_relationship_shows_both_directions(self):
        code, out = _run(self._args("show-relationship", "阿灯,老周"))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("阿灯 眼中的 老周（1）", out)
        self.assertIn("老周 眼中的 阿灯（1）", out)

    def test_relationship_requires_exactly_two_names(self):
        code, out = _run(self._args("show-relationship", "阿灯"))
        self.assertEqual(code, 1)
        self.assertIn("关系档案需要**两个**人物名", out)

    def test_relationship_without_archive_says_how_to_get_one(self):
        code, out = _run(self._args("show-relationship", "阿灯,无档案的人"))
        self.assertEqual(code, 1)
        self.assertIn("还没有关系档案", out)
        self.assertIn('persona run --names "阿灯,无档案的人"', out)

    # ------------------------------------------------------------ evidence

    def test_evidence_lists_sections_and_unmerged(self):
        code, out = _run(self._args("evidence", "阿灯"))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("■ 未并入档案的证据", out)
        self.assertIn("https://example.test/a", out)
        self.assertIn("定位 body[3:18]", out)

    def test_evidence_unknown_trait_exits_zero_and_lists_tags(self):
        code, out = _run(self._args("evidence", "阿灯", "--trait", "curious"))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("没有 trait_tag=curious 的证据", out)
        self.assertIn("reserved(2)", out)

    # -------------------------------------------------------------- export

    def test_export_defaults_to_stdout(self):
        code, out = _run(self._args("export", "阿灯"))
        self.assertEqual(code, EXIT_OK)
        self.assertTrue(out.startswith("## 阿灯"))

    def test_export_writes_a_file(self):
        target = Path(self._tmp) / "阿灯.md"
        code, out = _run(self._args("export", "阿灯", "--out", str(target)))
        self.assertEqual(code, EXIT_OK)
        self.assertIn("已写入", out)
        self.assertIn("## 阿灯", target.read_text(encoding="utf-8"))

    def test_export_rejects_json_with_a_reason(self):
        code, out = _run(self._args("export", "阿灯", "--format", "json"))
        self.assertEqual(code, 1)
        self.assertIn("本期只支持 --format md", out)


# ==========================================================================
# 帮助文本：两句话是产品承诺
# ==========================================================================


class HelpTextTest(unittest.TestCase):
    def _help(self, command: str) -> str:
        buf = io.StringIO()
        with contextlib.suppress(SystemExit):
            with redirect_stdout(buf):
                main([command, "--help"])
        return buf.getvalue()

    def test_run_help_keeps_the_two_sentences(self):
        """⑧ 号断言：这两句一被"精简"，用户就会开始乱调旋钮。

        P0 预算改为「软硬双限」后，旧的「两者取严」措辞已不准确——现在是
        「达软限暂停、达硬限才强制中断」，这句话同样承担着「旋钮很安全、别乱调」
        的产品承诺，故守卫改为断言新的预算说明句。
        """
        text = self._help("run")
        self.assertIn("99% 的用户不需要改这个", text)
        self.assertIn("达软限暂停", text)
        self.assertIn("达硬限才强制中断", text)

    def test_run_help_explains_adaptation_default(self):
        text = self._help("run")
        self.assertIn("省略时取「原著」", text)
        self.assertIn("清单会标注", text)

    def test_run_help_has_no_probe_flags(self):
        """§12 纪律 1：探针参数只属于 e2e 脚本，不许出现在产品命令里。"""
        text = self._help("run")
        for forbidden in ("--probe", "--replay-materials", "--allow-real"):
            self.assertNotIn(forbidden, text)

    def test_five_new_commands_are_registered(self):
        listing = io.StringIO()
        with contextlib.suppress(SystemExit):
            with redirect_stdout(listing):
                main([])
        text = listing.getvalue()
        for command in ("run", "show", "show-relationship", "evidence", "export",
                        "status"):
            self.assertIn(command, text)


# ==========================================================================
# run：环境与打桩
# ==========================================================================

_ENV_KEYS = (
    "PERSONA_DATA_DIR", "PERSONA_OFFLINE_ONLY", "PERSONA_MODEL_PROVIDER",
    "PERSONA_PROVIDER_API_KEY", "PERSONA_SEARCH_API_KEY", "PERSONA_MODEL_MAX_CALLS",
    "PERSONA_MODEL_MAX_COST", "PERSONA_MODEL_MAX_COST_PER_TASK", "PERSONA_PROVIDER",
    "PERSONA_MODEL_ID", "PERSONA_MODEL_CURRENCY", "PERSONA_MODEL_PRICE_IN_PER_1M",
    "PERSONA_MODEL_PRICE_OUT_PER_1M", "PERSONA_MODEL_PRICE_SOURCE",
    "PERSONA_MODEL_PRICE_CHECKED_AT", "PERSONA_MODEL_EST_TOKENS_IN",
    "PERSONA_MODEL_EST_TOKENS_OUT", "PERSONA_MODEL_REQUIRE_GATE_VERDICT",
    "PERSONA_RESEARCH_MAX_EXTRACTS",
    # 别名也要一起清掉：本机环境里可能已经有 TAVILY_API_KEY / DEEPSEEK_API_KEY，
    # 不清的话"缺密钥"这条测试会莫名为假（测试要能独立于开发机的环境变量）。
    "TAVILY_API_KEY", "DEEPSEEK_API_KEY",
)


class _RunEnv:
    """把 `run` 需要的那几个环境变量摆成"配好了密钥 + 有单价"，退出时原样还回去。

    刻意**不依赖仓库里的 `.env`**：测试要能在干净机器上跑，而且"单价缺失"本身
    就是一条要测的文案（见 `test_dry_needs_keys_too`）。
    """

    def __init__(self, root: str) -> None:
        self.root = root
        self.saved: dict[str, str | None] = {}

    def __enter__(self) -> "_RunEnv":
        values = {
            "PERSONA_DATA_DIR": self.root,
            "PERSONA_OFFLINE_ONLY": "false",
            "PERSONA_MODEL_PROVIDER": "deepseek",
            "PERSONA_PROVIDER_API_KEY": "sk-test-not-a-real-key",
            "PERSONA_SEARCH_API_KEY": "tvly-test-not-a-real-key",
            "PERSONA_MODEL_MAX_CALLS": "10",
            "PERSONA_MODEL_ID": "deepseek-chat",
            "PERSONA_MODEL_CURRENCY": "USD",
            "PERSONA_MODEL_PRICE_IN_PER_1M": "0.14",
            "PERSONA_MODEL_PRICE_OUT_PER_1M": "0.28",
            "PERSONA_MODEL_PRICE_SOURCE": "https://example.test/pricing",
            "PERSONA_MODEL_EST_TOKENS_IN": "6000",
            "PERSONA_MODEL_EST_TOKENS_OUT": "2000",
        }
        for key in _ENV_KEYS:
            self.saved[key] = os.environ.get(key)
            os.environ.pop(key, None)
        os.environ.update(values)
        return self

    def __exit__(self, *exc: object) -> None:
        for key, old in self.saved.items():
            if old is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = old


def _fake_plan(*, searches: int = 2, extractions: int = 1, dialogues: int = 1,
               cost: float = 0.0066):
    plan = mock.MagicMock()
    plan.searches = searches
    plan.est_search_credits = 2.2
    plan.extracts_upper_bound = 1
    plan.remediations = 1
    plan.extractions = extractions
    plan.dialogues = dialogues
    plan.est_model_cost = cost
    plan.currency = "USD"
    return plan


def _fake_stack():
    stack = mock.MagicMock()
    stack.claim_extractor = object()
    stack.warnings = []
    stack.describe.return_value = {"ledger": {"hydrated": False}}
    stack.ledger.records.return_value = []
    run = mock.MagicMock()
    run.status = "SUCCEEDED"
    run.task_id = "tsk_fake"
    run.task_status = "SUCCEEDED"
    run.steps = []
    run.source_ids = ["src_1"]
    run.evidence_ids = ["evd_1"]
    run.persona_profile_ids = ["pnf_1"]
    run.relationship_profile_ids = ["rlp_1"]
    run.is_complete.return_value = True
    stack.orchestrator.run.return_value = mock.MagicMock(
        __class__=type("R", (), {}), value=run)
    # `is_err(result)` / `unwrap(result)` 需要真正的 Ok
    from persona.core.result import Ok
    stack.orchestrator.run.return_value = Ok(run)
    return stack


class RunDryTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gps-p51-dry-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "data"

    def test_dry_prints_the_plan_and_leaves_nothing_behind(self):
        """④ 号断言：`--dry` 跑完 → 没有数据库文件、没有确认文件、退出码 0。"""
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--out", str(self.root), "--no-dotenv", "--dry"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("本次将要发出的真实调用", out)
        self.assertIn("边界", out)
        self.assertFalse((self.root / "persona.db").exists(), "--dry 不许建库")
        self.assertFalse(consent.consent_path(self.root).exists(), "--dry 不许写确认")

    def test_dry_marks_the_defaulted_adaptation(self):
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--out", str(self.root), "--no-dotenv", "--dry"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("红楼梦 / 原著（未指定，默认）", out)

    def test_explicit_adaptation_is_marked_as_explicit(self):
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--adaptation", "曹雪芹原著", "--out", str(self.root),
                              "--no-dotenv", "--dry"])
        self.assertEqual(code, EXIT_OK)
        self.assertIn("红楼梦 / 曹雪芹原著（显式指定）", out)
        self.assertNotIn("未指定，默认", out)

    def test_dry_reports_that_the_ledger_was_not_read(self):
        with _RunEnv(str(self.root)):
            _, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                           "--out", str(self.root), "--no-dotenv", "--dry"])
        self.assertIn("未读取（--dry 不碰数据库", out)

    def test_missing_search_key_stops_the_run(self):
        """没有检索出口 → 当场停：跑下去只会拿空材料花模型钱。"""
        with _RunEnv(str(self.root)):
            os.environ.pop("PERSONA_SEARCH_API_KEY", None)
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--out", str(self.root), "--no-dotenv", "--dry"])
        self.assertEqual(code, 1)
        self.assertIn("拒绝运行：本次计划里没有任何检索", out)
        self.assertIn("PERSONA_SEARCH_API_KEY", out)
        self.assertIn("本次**没有发起任何请求**", out)
        self.assertFalse((self.root / "persona.db").exists())

    def test_missing_model_key_stops_the_run(self):
        with _RunEnv(str(self.root)):
            os.environ.pop("PERSONA_PROVIDER_API_KEY", None)
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--out", str(self.root), "--no-dotenv", "--dry"])
        self.assertEqual(code, 1)
        self.assertIn("缺少模型密钥", out)
        self.assertIn("`--dry` 也要读配置", out)


class RunGuardTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gps-p51-guard-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "data"

    def test_fictional_character_needs_a_work_title(self):
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--out", str(self.root),
                              "--no-dotenv", "--dry"])
        self.assertEqual(code, 1)
        self.assertIn("虚构角色必须声明所属作品", out)

    def test_unknown_vocabulary_is_rejected_not_silently_defaulted(self):
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--vocabulary", "l9", "--out", str(self.root),
                              "--no-dotenv", "--dry"])
        self.assertEqual(code, 1)
        self.assertIn("未知词表：l9", out)

    def test_budget_below_estimate_is_refused_before_any_call(self):
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--budget", "0.0001", "--out", str(self.root),
                              "--no-dotenv", "--dry"])
        self.assertEqual(code, 1)
        self.assertIn("预算不足", out)
        self.assertIn("本次**没有发起任何请求**", out)

    def test_non_tty_without_yes_is_refused(self):
        """非交互环境必须显式 `--yes`（不能靠"没人反对"就算同意）。"""
        with _RunEnv(str(self.root)):
            code, out = _run(["run", "--names", "林黛玉", "--work-title", "红楼梦",
                              "--out", str(self.root), "--no-dotenv"])
        self.assertEqual(code, 1)
        self.assertIn("非交互环境", out)
        self.assertIn("--yes", out)
        self.assertFalse(consent.consent_path(self.root).exists())


class RunConsentTest(unittest.TestCase):
    """授权六分支：TTY 问一次 / 记住 / `n` / `--yes` / `--dry` / 删文件重问。

    成功路径全部打桩（`build_business_stack` / `plan_calls` / `register_targets`），
    所以这些测试不会联网、不会花钱。
    """

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory(prefix="gps-p51-run-")
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name) / "data"
        self.root.mkdir(parents=True, exist_ok=True)

        env = _RunEnv(str(self.root))
        env.__enter__()
        self.addCleanup(env.__exit__, None, None, None)

        patches = [
            mock.patch("persona.orchestrator.build_business_stack",
                       lambda *a, **k: __import__(
                           "persona.core.result", fromlist=["Ok"]).Ok(_fake_stack())),
            mock.patch("persona.orchestrator.plan_calls",
                       lambda *a, **k: _fake_plan()),
            mock.patch("persona.orchestrator.register_targets",
                       lambda *a, **k: __import__(
                           "persona.core.result", fromlist=["Ok"]).Ok(["pe_fake"])),
        ]
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)

    def _argv(self, *extra: str) -> list[str]:
        return ["run", "--names", "林黛玉", "--work-title", "红楼梦",
                "--out", str(self.root), "--no-dotenv", *extra]

    def _tty(self):
        return [
            mock.patch("persona.cli.main._stdin_is_tty", lambda: True),
            mock.patch("persona.cli.main._stdout_is_tty", lambda: True),
        ]

    def test_first_tty_run_asks_and_remembers(self):
        patches = self._tty()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch("builtins.input", return_value="y"):
            code, out = _run(self._argv())

        self.assertEqual(code, EXIT_OK)
        self.assertIn("将要发生 2 次检索 + 2 次模型调用", out)
        self.assertIn("继续？[y/N]", out)
        self.assertTrue(consent.is_confirmed(self.root))
        self.assertIn("结论", out)

    def test_second_run_does_not_ask_again(self):
        consent.remember(self.root, estimated_usd=0.0066)
        patches = self._tty()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch("builtins.input",
                        side_effect=AssertionError("已经确认过，不该再问")):
            code, out = _run(self._argv())
        self.assertEqual(code, EXIT_OK)
        self.assertIn("此前已确认过真实调用", out)
        self.assertNotIn("[y/N]", out)

    def test_answer_no_calls_nothing_and_writes_no_consent(self):
        patches = self._tty()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch("builtins.input", return_value="n"):
            code, out = _run(self._argv())
        self.assertEqual(code, 1)
        self.assertIn("已取消，没有发生任何调用", out)
        self.assertFalse(consent.is_confirmed(self.root))

    def test_empty_answer_is_treated_as_no(self):
        patches = self._tty()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch("builtins.input", return_value=""):
            code, out = _run(self._argv())
        self.assertEqual(code, 1)
        self.assertIn("已取消", out)

    def test_yes_flag_never_writes_consent(self):
        """脚本里的 `--yes` 不该替"人"做长期约定。"""
        code, out = _run(self._argv("--yes"))
        self.assertEqual(code, EXIT_OK)
        self.assertFalse(consent.is_confirmed(self.root),
                         "--yes 是这次同意，不是以后都同意")

    def test_deleting_the_file_asks_again(self):
        consent.remember(self.root, estimated_usd=0.01)
        consent.forget(self.root)
        patches = self._tty()
        for patch in patches:
            patch.start()
            self.addCleanup(patch.stop)
        with mock.patch("builtins.input", return_value="y"):
            _, out = _run(self._argv())
        self.assertIn("[y/N]", out)

    def test_consent_file_holds_only_three_keys(self):
        consent.remember(self.root, estimated_usd=0.0066)
        payload = json.loads(consent.consent_path(self.root).read_text(encoding="utf-8"))
        self.assertEqual(set(payload), set(consent.ALLOWED_KEYS))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
