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
            return config.get("llm", {}).get("api_key", "")
        except Exception:
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

            response = json.loads(result.stdout.decode("utf-8"))

            if "error" in response:
                logger.error(f"LLM 调用失败 [{agent_name}/{model_key}]: {response['error']}")
                return f"[LLM Error: {response['error'].get('message', 'unknown')}]"

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
            return f"[LLM Error: {e}]"

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
