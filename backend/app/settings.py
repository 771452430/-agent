"""应用配置与目录约定。

这里不用复杂的配置框架，而是保留一个轻量、易读的 settings 模块：
1. 方便学习时直接查看有哪些运行开关；
2. 所有路径都集中管理，避免在业务代码里散落硬编码；
3. 对外部模型与 LangSmith 只做“可选增强”，默认支持本地学习模式。
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class AppSettings:
    """集中定义项目运行期配置。"""

    app_name: str = "LangChain Learning Demo"
    root_dir: Path = Path(__file__).resolve().parents[2]
    backend_dir: Path = Path(__file__).resolve().parents[1]
    data_dir: Path = Path(__file__).resolve().parents[1] / "data"
    uploads_dir: Path = Path(__file__).resolve().parents[1] / "data" / "uploads"
    sqlite_path: Path = Path(__file__).resolve().parents[1] / "data" / "learning_demo.sqlite3"
    chroma_dir: Path = Path(__file__).resolve().parents[1] / "data" / "chroma"
    allow_mock_model: bool = True
    default_provider: str = "mock"
    default_model: str = "learning-mode"
    default_temperature: float = 0.2
    default_max_tokens: int = 1024
    cors_origins: tuple[str, ...] = ("http://localhost:3000", "http://127.0.0.1:3000")
    langsmith_project: str | None = os.getenv("LANGSMITH_PROJECT")

    def ensure_directories(self) -> None:
        """启动时准备本地数据目录。"""

        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.uploads_dir.mkdir(parents=True, exist_ok=True)
        self.chroma_dir.mkdir(parents=True, exist_ok=True)


def load_settings() -> AppSettings:
    """返回设置实例，并保证目录已创建。"""

    settings = AppSettings()
    settings.ensure_directories()
    return settings
