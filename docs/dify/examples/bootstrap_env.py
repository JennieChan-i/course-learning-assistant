"""Initialize a fresh Dify 1.18.0 deployment's secrets once, without printing values.

Documentation helper, not an installer. Never run against a previously started deployment.
"""

import argparse
from pathlib import Path
import re
import secrets

MARKER = "# COURSE_ASSISTANT_DIFY_ENV_INITIALIZED"
ROOT = Path(__file__).resolve().parents[3]


def initialize(directory: Path):
    directory = directory.resolve()
    target = directory / ".env"
    text = target.read_text(encoding="utf-8")
    if MARKER in text:
        raise ValueError("该配置已经初始化；不旋转已有密钥，请直接使用原配置。")
    volumes = directory / "volumes"
    if volumes.exists() and any(volumes.rglob("*")):
        raise ValueError("检测到已有数据目录。此脚本仅用于首次启动前，不能覆盖已有部署密钥。")
    names = (
        "SECRET_KEY",
        "DB_PASSWORD",
        "REDIS_PASSWORD",
        "PLUGIN_DAEMON_KEY",
        "PLUGIN_DIFY_INNER_API_KEY",
        "DIFY_AGENT_API_TOKEN",
        "DIFY_AGENT_SERVER_SECRET_KEY",
        "DIFY_AGENT_LOCAL_SANDBOX_AUTH_TOKEN",
    )
    values = {key: secrets.token_hex(32) for key in names}
    # Agent backend requires unpadded base64url decoding to exactly 32 bytes.
    values["DIFY_AGENT_SERVER_SECRET_KEY"] = secrets.token_urlsafe(32)
    sandbox_key = secrets.token_hex(32)
    values.update(SANDBOX_API_KEY=sandbox_key, CODE_EXECUTION_API_KEY=sandbox_key)
    vector_key = secrets.token_hex(32)
    values.update(WEAVIATE_API_KEY=vector_key, WEAVIATE_AUTHENTICATION_APIKEY_ALLOWED_KEYS=vector_key)
    for key, value in values.items():
        pattern = rf"^{re.escape(key)}=.*$"
        if not re.search(pattern, text, flags=re.MULTILINE):
            raise ValueError(f"模板缺少 {key}；请核对 Dify 版本，不应跳过。")
        text = re.sub(pattern, f"{key}={value}", text, flags=re.MULTILINE)
    target.write_text(text.rstrip() + "\n" + MARKER + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--docker-directory", type=Path, default=ROOT / "runtime/dify/source/docker")
    args = parser.parse_args()
    initialize(args.docker_directory)
    print("已初始化首次部署配置；未输出密钥。请保留 .env，后续不要重新生成。")
