"""授权确认（P5.1）：真实调用要过一次人，然后记住这个人同意过。

**为什么不是 `--allow-real`**：那个参数是开发者话术——用户看不懂，只会复制
粘贴一个自己不理解的东西。产品里的规则是：**密钥在 = 具备条件**，但花钱这件事
必须至少被看见一次。

**记忆的边界（三件事刻意不做）**：

1. **只记"同意过真实调用"，不记"同意过某个金额"**——金额每次运行都在清单里
   重新打印。记金额等于让上次的 0.25 替这次的 5 个名字背书。
2. **只存三项**（`version` / `confirmed_at` / `estimated_usd`）：不存密钥、不存
   命令、不存材料、不存名字。这个文件要能被随手打开看一眼而不泄露任何东西。
3. **`--yes` 不写这个文件**：脚本里写一个 `--yes` 就应该一直有效；而"人"记得的
   确认不该被脚本继承（否则 CI 里跑过一次，人再跑就静默花钱了）。

文件是 `<数据目录>/.persona_consent.json`——**普通文件，不是数据库记录**，
所以"不改数据模型"这条没有破例。
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

from ..core.temporal import to_iso, utc_now

#: 文件名（放在数据目录里，和数据目录一起被备份/删除）
CONSENT_FILENAME = ".persona_consent.json"

#: 格式版本：将来要加字段时靠它判断"这是老文件"，而不是靠字段是否存在去猜
CONSENT_VERSION = 1

#: 只允许出现的三个键。多一个都算泄露面（测试会盯着这一点）
ALLOWED_KEYS = frozenset({"version", "confirmed_at", "estimated_usd"})


def consent_path(out_dir: str | Path) -> Path:
    return Path(out_dir) / CONSENT_FILENAME


def load_consent(out_dir: str | Path) -> dict[str, Any] | None:
    """读确认记录。文件不存在 / 坏 JSON / 版本不认识 → `None`（重新问一次）。

    **不抛异常**：确认文件的唯一职责是"少问一次"，它坏了最多多问一次，
    绝不能因为这个文件损坏让 `run` 崩掉。
    """
    path = consent_path(out_dir)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict):
        return None
    if int(raw.get("version") or 0) != CONSENT_VERSION:
        return None
    if not str(raw.get("confirmed_at") or "").strip():
        return None
    return {key: raw[key] for key in ALLOWED_KEYS if key in raw}


def is_confirmed(out_dir: str | Path) -> bool:
    """这个数据目录里有没有"同意过真实调用"的记录。"""
    return load_consent(out_dir) is not None


def remember(out_dir: str | Path, *, estimated_usd: float | None,
             now: datetime | None = None) -> Path:
    """写确认记录（先写临时文件再替换，避免半截 JSON）。"""
    directory = Path(out_dir)
    directory.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "version": CONSENT_VERSION,
        "confirmed_at": to_iso(now or utc_now()),
        "estimated_usd": None if estimated_usd is None else round(float(estimated_usd), 6),
    }
    path = consent_path(directory)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(payload, ensure_ascii=False, indent=2),
                         encoding="utf-8")
    temporary.replace(path)
    return path


def forget(out_dir: str | Path) -> bool:
    """删掉确认记录（重新启用询问）。文件不存在返回 `False`，不报错。"""
    path = consent_path(out_dir)
    try:
        path.unlink()
        return True
    except FileNotFoundError:
        return False


def build_question(*, searches: int, model_calls: int, estimated_usd: float | None,
                   budget_usd: float | None, currency: str = "USD") -> str:
    """确认问句。**数字全部来自调用方（`plan_calls`），这里不自己算。**

    `estimated_usd is None` 时**不填 0**：估不出来就说估不出来，
    用 0 代替会让用户以为"这次不要钱"。
    """
    lead = f"将要发生 {int(searches)} 次检索 + {int(model_calls)} 次模型调用"
    if estimated_usd is None:
        cost = "预估费用未知（缺单价或 token 估算）"
    else:
        cost = f"预估 {float(estimated_usd):.3f} {currency}"
    if budget_usd is not None:
        return f"{lead}，{cost}（单任务上限 {float(budget_usd):.2f} {currency}）。继续？[y/N]"
    return f"{lead}，{cost}。继续？[y/N]"


def previous_note(out_dir: str | Path) -> str:
    """已确认过时打印的一行（带可逆操作，不让人以为"再也问不了了"）。"""
    record = load_consent(out_dir)
    when = str((record or {}).get("confirmed_at") or "")
    return (f"（此前已确认过真实调用{('，' + when) if when else ''}，本次不再询问；"
            f"重新启用询问请删掉 {consent_path(out_dir)}）")
