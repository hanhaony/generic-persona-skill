"""静态守卫：禁止"吐字残留"混进对外文档与代码（离线，零外呼）。

为什么需要这条守卫：这类残留**肉眼和工具渲染都看不见**。
P6.4 写 `docs/P6_TO_P7_HANDOFF.md` 时，一行里混进了 `模板<｜ISSUE_TEMPLATE`
（`<` + 全角竖线 U+FF5C + 隐藏分词残片），Read/Edit 的渲染里它显示得跟
普通文字一模一样，肉眼校对也看不出来——最后是靠把整行**逐字符打码点**
才揪出来的（`print(' '.join('%02x' % ord(c) for c in line))`）。
同类残片还包括 `hy_place`、`▁`（U+2581）。

**为什么必须静态扫描兜底**：它们通常落在文档表格或 docstring 里，
**不参与执行**，所以语法检查、import、全量回归**全都会绿**——
唯一会受影响的是读到它的人（对外文档读起来怪，或者误导读者）。

守卫扫三样：
    ① **硬模式**（出现即 FAIL）：`hy_place` / `<｜` / `▁`
    ② **上下文判断**（全角｜紧贴 ASCII 结构字符才 FAIL）：
       `{"type":"enabled"｜"disabled"}` 这种是残留（本该是 `/`），
       而图例里的 `✅ 允许｜👁 只读`、日期后的 `2026-10-17 ｜ 明细见`
       是**正当用法**，必须放行——**宁可少杀，不能误杀正当的图例分隔符**。
    ③ **允许清单完整性**：3 处已知正当用法若被别人改掉，本测试会提醒清清单，
       避免清单越攒越假。

作用域：`persona/**/*.py` + `docs/**/*.md`（对外文档与运行时代码）。
scripts/ 与 tests/ 不扫：它们不对外，且扫了会与变异探针互相干扰。
"""

from __future__ import annotations

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# 作用域：对外文档 + 运行时代码
SCAN_SPECS = (
    (ROOT / "persona", "*.py"),
    (ROOT / "docs", "*.md"),
)

# ① 硬模式：出现一次即 FAIL
HARD_PATTERNS = (
    ("hy_place", "hy_place 残片"),
    ("<\uff5c", "<｜ 序列"),
    ("\u2581", "▁(U+2581) 残片"),
)

# ② 全角｜紧跟这些 ASCII 结构字符 = 残留（该用 / 或逗号的地方用了全角竖线）
ASCII_STRUCTURAL = '"\'{}<>'

# ③ 已知正当用法（图例分隔符 / 日期分隔），每行存**锚点**而不是行号——
#    行号会随文档增删漂移，锚点不会。
ALLOWED_LEGEND_PIPES = (
    ("docs/ARCHITECTURE.md", "✅ 允许｜👁 只读｜⛔ 禁止"),
    ("docs/DATA_MODEL.md", "> 图例：✅ 必填 ｜ ⭕ 可选"),
    ("docs/RUNNING.md", "**登记日期**：2026-10-17 ｜ 明细见"),
)

PROBE_RELPATH = "docs/_artifact_guard_probe.md"

# 报错里必须带这句提示：**想在文档里记录这些模式时，只描述、不写字面量**——
# 否则本守卫会把"记录它"这件事本身当成"混进残留"，把自己绕死。
# （上线当天就发生过：RUNNING.md 的变更历史行里写了三个字面量，被自己抓红。）
_REPORT_HINT = (
    "发现吐字残留（打码点才看得见，别用肉眼）：\n"
    "⚠️ 若这一行是在**记录**这些模式，请改成描述（如「分词残片 / 尖括号紧跟全角竖线 / U+2581」），"
    "**不要写字面量**——本守卫不区分「记录」与「混入」。\n")


def _iter_files():
    for base, pattern in SCAN_SPECS:
        for path in sorted(base.rglob(pattern)):
            if ".git" in path.parts or "__pycache__" in path.parts:
                continue
            yield path


def _find_hard_artifacts() -> list[str]:
    """① 硬模式命中清单，形如 `docs/X.md:12: hy_place 残片`。"""
    found: list[str] = []
    for path in _iter_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for pattern, label in HARD_PATTERNS:
                if pattern in line:
                    found.append(f"{path.relative_to(ROOT)}:{lineno}: {label}")
    return found


def _find_structural_pipe() -> list[str]:
    """② 全角｜紧贴 ASCII 结构字符 = 残留；图例/日期里的正当用法放行。"""
    found: list[str] = []
    for path in _iter_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), 1):
            for idx, ch in enumerate(line):
                if ch != "\uff5c":
                    continue
                before = line[idx - 1] if idx > 0 else ""
                after = line[idx + 1] if idx + 1 < len(line) else ""
                if before in ASCII_STRUCTURAL or after in ASCII_STRUCTURAL:
                    found.append(f"{path.relative_to(ROOT)}:{lineno}: 全角｜紧贴 ASCII 结构字符")
                    break
    return found


class TestTextArtifactGuard(unittest.TestCase):
    """对外文档与运行时代码里，不允许出现吐字残留。"""

    def test_no_hard_artifacts(self):
        found = _find_hard_artifacts()
        self.assertEqual([], found, _REPORT_HINT + "\n".join(found))

    def test_no_pipe_adjacent_to_ascii_structural(self):
        found = _find_structural_pipe()
        self.assertEqual([], found, _REPORT_HINT + "\n".join(found))

    def test_allowlisted_legend_pipes_still_exist(self):
        """允许清单不许腐烂：锚点必须还在，且**恰好一份**（防重复粘贴）。"""
        for rel, anchor in ALLOWED_LEGEND_PIPES:
            path = ROOT / rel
            self.assertTrue(path.is_file(), f"允许清单指向的文件不存在：{rel}")
            text = path.read_text(encoding="utf-8", errors="replace")
            self.assertEqual(
                1, text.count(anchor),
                f"{rel} 的允许锚点出现 {text.count(anchor)} 次（应为 1）：{anchor}。"
                f"文档改过了就同步清清单，别让清单越攒越假")
            self.assertIn("\uff5c", anchor, f"{rel} 的锚点里没有全角｜，清单写错了")


class TestTextArtifactGuardMutation(unittest.TestCase):
    """变异验证：故意塞进残留，守卫**必须红**。

    必须是**文件级改写 + 子进程跑**（见 `docs/RUNNING.md` §5.44.4 教训②）：
    进程内改内存没用——扫描函数在 import 时就把路径算好了，
    而且探针文件若留在磁盘上，下一次全量回归会把整个仓库打红。
    所以探针写在 `docs/` 里（让守卫真能扫到），`finally` 里无条件删掉。
    """

    def _run_guard_case(self, case: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [sys.executable, "-m", "unittest", f"tests.test_text_artifact_guard.{case}"],
            cwd=str(ROOT), capture_output=True, text=True)

    def _with_probe(self, content: str, case: str) -> None:
        """写入探针 → 子进程跑守卫 → 断言它红 → `finally` 删掉探针。"""
        probe = ROOT / PROBE_RELPATH
        probe.write_text(content, encoding="utf-8")
        try:
            proc = self._run_guard_case(case)
            # unittest 的汇总输出在 **stderr**，不在 stdout
            output = (proc.stderr or "") + (proc.stdout or "")
            self.assertNotEqual(
                0, proc.returncode,
                "塞进残留后守卫居然还是绿的——守卫没生效。输出：\n" + output)
            self.assertIn("FAILED", output, "守卫没报 FAILED，可能是别的原因导致非零退出")
        finally:
            probe.unlink(missing_ok=True)
        self.assertFalse(probe.exists(), "探针没删干净，会污染下一次回归")

    def test_hard_patterns_are_red_when_injected(self):
        self._with_probe(
            "# 探针\n\n这里混进了 hy_place 残片、<｜ 序列与 ▁▁▁。\n",
            "TestTextArtifactGuard.test_no_hard_artifacts")

    def test_structural_pipe_is_red_when_injected(self):
        self._with_probe(
            "# 探针\n\n| `thinking` | `{\"type\":\"enabled\"｜\"disabled\"}` |\n",
            "TestTextArtifactGuard.test_no_pipe_adjacent_to_ascii_structural")

    def test_legend_pipe_is_not_killed(self):
        """反向变异：图例/日期里的正当用法**不许被误杀**（这是本守卫最容易做过头的地方）。"""
        self._with_probe_expected_green(
            "# 探针\n\n> 图例：✅ 必填 ｜ ⭕ 可选\n\n**登记日期**：2026-10-17 ｜ 明细见 docs/RUNNING.md\n")

    def _with_probe_expected_green(self, content: str) -> None:
        probe = ROOT / PROBE_RELPATH
        probe.write_text(content, encoding="utf-8")
        try:
            proc = self._run_guard_case("TestTextArtifactGuard.test_no_pipe_adjacent_to_ascii_structural")
            output = (proc.stderr or "") + (proc.stdout or "")
            self.assertEqual(
                0, proc.returncode,
                "正当的全角｜被误杀了——守卫做过头。输出：\n" + output)
        finally:
            probe.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
