"""API Key 读写的契约测试。

**为什么单独给这件事写测试**：曾经出过一个只在打包后才暴露的 bug ——

    server/config.py   写/读  ~/.shenlun/.env    ← 界面「设置」用这个
    llm.py             只读    <代码目录>/.env    ← 调模型用这个

用户在界面上配完 Key，界面显示「已配置」，但一批改就报
「未找到 DEEPSEEK_API_KEY」。源码模式下 <代码目录>/.env 恰好存在
（开发者自己建的），所以本地永远测不出来。

现在实现只有一处（`llm.py`），`server/config.py` 转出。这里的测试
锁住这个契约：**存进去的，读得出来；两边读的是同一处。**
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
for p in (ROOT, ROOT / "server"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

import llm                                          # noqa: E402


@pytest.fixture
def clean_env(tmp_path, monkeypatch):
    """把 Key 的读写都引到临时目录，并清掉环境变量。

    ⚠️ **两个路径都要替换。**
       只换 USER_ENV 不够 —— CODE_ENV 指向开发者本机的
       <项目根>/.env，那里通常有真 Key，测试会读到它：
         · 结果不可复现（换台机器就挂）
         · 「找不到 Key」这类用例永远测不过
    """
    monkeypatch.setattr(llm, "APP_DIR", tmp_path)
    monkeypatch.setattr(llm, "USER_ENV", tmp_path / ".env")
    monkeypatch.setattr(llm, "CODE_ENV", tmp_path / "code.env")
    monkeypatch.delenv(llm.KEY_NAME, raising=False)
    return tmp_path


# ══════════════════════════════════════════════════════════
# 往返：存进去要读得出来
# ══════════════════════════════════════════════════════════
def test_saved_key_can_be_read_back(clean_env):
    """核心契约。这个测试挂掉，说明界面配的 Key 用不上。"""
    llm.save_key("sk-roundtrip-test-key")
    assert llm.load_key() == "sk-roundtrip-test-key"


def test_save_writes_to_user_dir(clean_env):
    p = llm.save_key("sk-x")
    assert p == clean_env / ".env"
    assert p.exists()


def test_save_preserves_other_lines(clean_env):
    """保存 Key 不该把 .env 里别人的配置冲掉。"""
    env = clean_env / ".env"
    env.write_text("SOME_OTHER=value\n", encoding="utf-8")
    llm.save_key("sk-new")
    text = env.read_text(encoding="utf-8")
    assert "SOME_OTHER=value" in text
    assert "sk-new" in text


def test_save_empty_clears_key(clean_env):
    llm.save_key("sk-temp")
    llm.save_key("")
    assert llm.load_key() == ""
    assert "sk-temp" not in (clean_env / ".env").read_text(encoding="utf-8")


# ══════════════════════════════════════════════════════════
# 优先级
# ══════════════════════════════════════════════════════════
def test_env_var_wins(clean_env, monkeypatch):
    llm.save_key("sk-from-file")
    monkeypatch.setenv(llm.KEY_NAME, "sk-from-env")
    assert llm.load_key() == "sk-from-env"


def test_user_dir_wins_over_code_dir(clean_env, monkeypatch):
    code_env = clean_env / "code.env"
    code_env.write_text(f"{llm.KEY_NAME}=sk-code-dir\n", encoding="utf-8")
    monkeypatch.setattr(llm, "key_paths",
                        lambda: [llm.USER_ENV, code_env])

    llm.save_key("sk-user-dir")
    assert llm.load_key() == "sk-user-dir"


def test_export_prefix_is_parsed(clean_env, monkeypatch):
    """兼容 `export KEY=...` 的写法。"""
    p = clean_env / "x.env"
    p.write_text(f"export {llm.KEY_NAME}='sk-with-export'\n", encoding="utf-8")
    monkeypatch.setattr(llm, "key_paths", lambda: [p])
    assert llm.load_key() == "sk-with-export"


def test_missing_key_returns_empty_not_raise(clean_env):
    """load_key 找不到时返回空串。

    服务层靠这个判断「配了没」，来决定要不要提示用户去配 ——
    所以它不能抛异常。
    """
    assert llm.load_key() == ""


def test_load_key_raises_with_guidance(clean_env):
    """但真正要调模型时（_load_key）必须抛，而且错误信息要指路。"""
    with pytest.raises(llm.LLMError) as exc:
        llm._load_key()
    msg = str(exc.value)
    assert llm.KEY_NAME in msg
    assert "设置" in msg                      # 要告诉用户去哪配


# ══════════════════════════════════════════════════════════
# 两边必须指向同一处（就是这个契约曾经被破坏）
# ══════════════════════════════════════════════════════════
def test_config_and_llm_point_to_same_file(clean_env):
    """界面写的地方，必须是 llm 读的地方之一。

    ⚠️ 这是那个 bug 的直接复现条件 —— 破坏它就等于让用户在界面上
       白配一次 Key。
    """
    import config as cfg
    assert cfg.user_env_path() in llm.key_paths()


def test_config_and_llm_return_same_value(clean_env):
    import config as cfg
    llm.save_key("sk-consistency")
    assert cfg.load_api_key() == llm.load_key() == "sk-consistency"


def test_mask_never_reveals_full_key():
    import config as cfg
    key = "sk-abcdefghijklmnopqrstuvwxyz"
    masked = cfg.mask(key)
    assert key not in masked
    assert "…" in masked
