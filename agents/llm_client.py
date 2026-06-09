#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
LLM Client - 多模型调用客户端
支持 DeepSeek API 和其他 OpenAI 兼容接口
"""

import json
import os
import subprocess
import logging
from typing import Dict, List, Optional
from pathlib import Path

logger = logging.getLogger(__name__)

# 模型配置
MODEL_CONFIGS = {
    "deepseek-v4-flash": {
        "name": "deepseek-v4-flash",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-flash",
        "cost_per_1m_input": 1.0,
        "cost_per_1m_output": 2.0,
        "description": "快速，适合数据处理、文本分析、一般推理"
    },
    "deepseek-v4-pro": {
        "name": "deepseek-v4-pro",
        "base_url": "https://api.deepseek.com/v1/chat/completions",
        "model": "deepseek-v4-pro",
        "cost_per_1m_input": 12.0,
        "cost_per_1m_output": 24.0,
        "description": "强推理，适合复杂逻辑、策略决策、风险评估"
    }
}

# Agent 模型分配
AGENT_MODEL_MAP = {
    "market_analyst": "deepseek-v4-flash",      # 快速数据处理
    "execution_planner": "deepseek-v4-pro",     # 复杂交易推理
    "risk_controller": "deepseek-v4-pro",       # 关键风控判断
    "review_agent": "deepseek-v4-flash",        # 复盘分析
    "strategy_maintainer": "deepseek-v4-pro",   # 策略迭代决策
    "overfitting_auditor": "deepseek-v4-pro",   # 审计：过拟合检测
    "risk_auditor": "deepseek-v4-pro",          # 审计：风险评估
    "cost_execution_auditor": "deepseek-v4-pro", # 审计：成本执行
}


class LLMClient:
    """多模型 LLM 客户端"""

    def __init__(self, api_key: str = None, config_path: str = None):
        """初始化客户端

        Args:
            api_key: DeepSeek API Key（优先级最高）
            config_path: 配置文件路径
        """
        self.api_key = api_key or self._load_api_key(config_path)
        if not self.api_key:
            raise ValueError("未提供 DeepSeek API Key")

    def _load_api_key(self, config_path: str = None) -> str:
        """从配置文件或环境变量加载 API Key"""
        # 1. 环境变量
        key = os.environ.get("DEEPSEEK_API_KEY")
        if key:
            return key

        # 2. 配置文件
        if config_path is None:
            config_path = os.path.join(os.path.dirname(__file__), "config.json")
        try:
            with open(config_path, 'r') as f:
                config = json.load(f)
            key = config.get("llm", {}).get("api_key", "")
            if key:
                return key
        except Exception:
            pass

        # 3. Hermes CLI 统一配置（避免在 repo-local config.json 写入密钥）
        return self._load_hermes_api_key() or self._load_hermes_env_api_key()

    def _load_hermes_api_key(self) -> str:
        """Best-effort read of ~/.hermes/config.yaml without adding a YAML dependency."""
        path = Path.home() / ".hermes" / "config.yaml"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if stripped.startswith("api_key:"):
                    return stripped.split(":", 1)[1].strip().strip('"').strip("'")
        except Exception:
            return ""
        return ""

    def _load_hermes_env_api_key(self) -> str:
        """Best-effort read of ~/.hermes/.env for DEEPSEEK_API_KEY."""
        path = Path.home() / ".hermes" / ".env"
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                stripped = line.strip()
                if not stripped or stripped.startswith("#") or "=" not in stripped:
                    continue
                key, value = stripped.split("=", 1)
                if key.strip() == "DEEPSEEK_API_KEY":
                    return value.strip().strip('"').strip("'")
        except Exception:
            return ""
        return ""

    def call(self, agent_name: str, system_prompt: str, user_message: str,
             temperature: float = 0.3, max_tokens: int = 2000) -> str:
        """调用 LLM

        Args:
            agent_name: agent 名称（用于选择模型）
            system_prompt: 系统提示词
            user_message: 用户消息
            temperature: 温度参数
            max_tokens: 最大输出 token 数

        Returns:
            LLM 的文本回复
        """
        model_key = AGENT_MODEL_MAP.get(agent_name, "deepseek-v4-flash")
        model_config = MODEL_CONFIGS[model_key]

        payload = {
            "model": model_config["model"],
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_message}
            ],
            "temperature": temperature,
            "max_tokens": max_tokens
        }

        try:
            result = subprocess.run(
                ["curl", "-s", "-m", "60",
                 "-H", "Content-Type: application/json",
                 "-H", f"Authorization: Bearer {self.api_key}",
                 "-d", json.dumps(payload, ensure_ascii=False),
                 model_config["base_url"]],
                capture_output=True, timeout=90
            )

            stdout = result.stdout.decode("utf-8", errors="replace").strip()
            if result.returncode != 0 or not stdout:
                stderr = result.stderr.decode("utf-8", errors="replace").strip()
                logger.warning(
                    f"LLM HTTP 调用无有效响应 [{agent_name}/{model_key}]: "
                    f"returncode={result.returncode}, stderr={stderr[:200]}"
                )
                return self._call_hermes_cli(agent_name, system_prompt, user_message)

            try:
                response = json.loads(stdout)
            except json.JSONDecodeError as e:
                logger.warning(
                    f"LLM HTTP 响应不是 JSON [{agent_name}/{model_key}]: {e}; fallback hermes cli"
                )
                return self._call_hermes_cli(agent_name, system_prompt, user_message)

            if "error" in response:
                logger.error(f"LLM 调用失败 [{agent_name}/{model_key}]: {response['error']}")
                return self._call_hermes_cli(agent_name, system_prompt, user_message)

            content = response.get("choices", [{}])[0].get("message", {}).get("content", "")
            usage = response.get("usage", {})
            logger.info(
                f"LLM 调用成功 [{agent_name}/{model_key}]: "
                f"input={usage.get('prompt_tokens', '?')}, "
                f"output={usage.get('completion_tokens', '?')}"
            )
            return content

        except subprocess.TimeoutExpired:
            logger.error(f"LLM 调用超时 [{agent_name}/{model_key}]")
            return "[LLM Error: timeout]"
        except Exception as e:
            logger.error(f"LLM 调用异常 [{agent_name}/{model_key}]: {e}")
            return self._call_hermes_cli(agent_name, system_prompt, user_message)

    def _call_hermes_cli(self, agent_name: str, system_prompt: str, user_message: str) -> str:
        """Fallback through the user's configured Hermes CLI provider."""
        prompt = (
            f"System role for {agent_name}:\n{system_prompt}\n\n"
            f"User message:\n{user_message}"
        )
        try:
            result = subprocess.run(
                ["hermes", "chat", "-Q", "-q", prompt],
                capture_output=True,
                text=True,
                timeout=180,
            )
        except subprocess.TimeoutExpired:
            logger.error(f"Hermes CLI fallback 超时 [{agent_name}]")
            return "[LLM Error: hermes cli timeout]"
        except Exception as e:
            logger.error(f"Hermes CLI fallback 异常 [{agent_name}]: {e}")
            return f"[LLM Error: hermes cli {e}]"

        output = result.stdout.strip()
        lines = [
            line for line in output.splitlines()
            if not line.strip().startswith("session_id:")
        ]
        content = "\n".join(lines).strip()
        if result.returncode != 0 or not content:
            stderr = result.stderr.strip()
            logger.error(
                f"Hermes CLI fallback 失败 [{agent_name}]: "
                f"returncode={result.returncode}, stderr={stderr[:200]}"
            )
            return f"[LLM Error: hermes cli failed: {stderr[:200]}]"
        logger.info(f"Hermes CLI fallback 成功 [{agent_name}]")
        return content

    def call_json(self, agent_name: str, system_prompt: str, user_message: str,
                  temperature: float = 0.1, max_tokens: int = 2000) -> Dict:
        """调用 LLM 并解析 JSON 响应

        Returns:
            解析后的 JSON 字典，解析失败返回 {"error": ..., "raw": ...}
        """
        raw = self.call(agent_name, system_prompt, user_message, temperature, max_tokens)

        # 尝试从 markdown code block 中提取 JSON
        import re
        json_match = re.search(r'```(?:json)?\s*\n?(.*?)\n?```', raw, re.DOTALL)
        json_str = json_match.group(1).strip() if json_match else raw.strip()

        try:
            return json.loads(json_str)
        except json.JSONDecodeError:
            return {"error": "JSON parse failed", "raw": raw}
