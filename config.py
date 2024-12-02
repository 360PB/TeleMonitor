import os
from dotenv import load_dotenv

# 加载 .env 文件
load_dotenv()

class Config:
    # Telegram 配置
    TELEGRAM_API_ID = os.getenv('TELEGRAM_API_ID')
    TELEGRAM_API_HASH = os.getenv('TELEGRAM_API_HASH')
    SESSION_NAME = 'quark_session'
    DEFAULT_CHANNEL = os.getenv('DEFAULT_CHANNEL', '@NewQuark')

    # 代理配置
    PROXY_ENABLED = os.getenv('PROXY_ENABLED', 'false').lower() == 'true'
    PROXY_TYPE = os.getenv('PROXY_TYPE', 'http')
    PROXY_ADDRESS = os.getenv('PROXY_ADDRESS', '127.0.0.1')
    PROXY_PORT = int(os.getenv('PROXY_PORT', 7890))

config = Config()