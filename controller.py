import asyncio
import os
import re
import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone

import aiosqlite
import socks
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaPhoto

from config import config

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

class TelegramMessageController:
    def __init__(self, config):
        self.config = config
        self.db_path = 'messages.db'
        self.client = None
        self.db_lock = asyncio.Lock()  # 添加数据库锁

    async def init_db(self, retry_count=3):
        """健壮的数据库初始化"""
        for attempt in range(retry_count):
            try:
                async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                    await db.execute('''
                        CREATE TABLE IF NOT EXISTS messages (
                            id INTEGER PRIMARY KEY AUTOINCREMENT,
                            name TEXT,
                            description TEXT,
                            link TEXT UNIQUE,
                            file_size TEXT,
                            tags TEXT,
                            timestamp TEXT,
                            image_path TEXT
                        )
                    ''')
                    await db.commit()
                    logger.info("数据库初始化成功")
                    return
            except Exception as e:
                logger.warning(f"数据库初始化尝试 {attempt + 1} 失败: {e}")
                await asyncio.sleep(1)

        logger.error("无法初始化数据库")

    async def create_client(self):
        """创建并启动 Telegram 客户端"""
        if not self.client:
            proxy = None
            if config.PROXY_ENABLED:
                proxy = (
                    socks.HTTP if config.PROXY_TYPE == "http" else socks.SOCKS5,
                    config.PROXY_ADDRESS,
                    config.PROXY_PORT
                )

            self.client = TelegramClient(
                config.SESSION_NAME,
                config.TELEGRAM_API_ID,
                config.TELEGRAM_API_HASH,
                proxy=proxy
            )

            try:
                await self.client.start()
                logger.info("Telegram 客户端启动成功")
            except Exception as e:
                logger.error(f"Telegram 客户端启动失败: {e}")
                self.client = None

        return self.client

    async def fetch_channel_history(self, channel_name=None, limit=100, offset_date=None):
        """健壮的历史消息获取"""
        if not channel_name:
            channel_name = config.DEFAULT_CHANNEL

        try:
            client = await self.create_client()

            if not client:
                logger.error("无法创建 Telegram 客户端")
                return False

            try:
                entity = await client.get_entity(channel_name)
            except Exception as e:
                logger.error(f"获取频道实体失败: {e}")
                return False

            try:
                messages = await client.get_messages(entity, limit=limit, offset_date=offset_date)

                for message in messages:
                    try:
                        media_path = await self.save_media(client, message)
                        parsed_message = self.parse_message(message)

                        if parsed_message:
                            async with self.db_lock:
                                await self.insert_message(parsed_message, media_path)
                    except Exception as msg_error:
                        logger.error(f"处理单条消息时出错: {msg_error}")

                logger.info(f"已获取并处理了 {len(messages)} 条历史消息")
                return True

            except FloodWaitError as flood:
                logger.error(f"Telegram API 限流，等待 {flood.seconds} 秒")
                await asyncio.sleep(flood.seconds)
                return False
            except Exception as e:
                logger.error(f"获取历史消息出错: {e}")
                return False

        except Exception as e:
            logger.error(f"获取历史消息的整体流程出错: {e}")
            return False

    @staticmethod
    def extract_quark_link(message_content: str) -> Optional[str]:
        """从消息内容中提取夸克链接"""
        match = re.search(r'https://pan\.quark\.cn/s/[a-zA-Z0-9]+', message_content)
        return match.group(0) if match else None

    async def save_media(self, client, message) -> Optional[str]:
        """保存 Telegram 消息中的媒体文件到本地"""
        try:
            if isinstance(message.media, MessageMediaPhoto):
                folder = "media"
                os.makedirs(folder, exist_ok=True)
                file_path = os.path.join(folder, f"{message.id}.jpg")
                await client.download_media(message, file_path)
                return file_path
        except Exception as e:
            logger.error(f"图片下载失败: {e}")
        return None

    @staticmethod
    def convert_to_local_time(utc_datetime):
        """将 UTC 时间转换为本地时间"""
        local_timezone = datetime.now(timezone.utc).astimezone().tzinfo
        return utc_datetime.astimezone(local_timezone)

    def parse_message(self, message):
        """解析 Telegram 消息内容"""
        try:
            message_content = message.message or ""
            name_match = re.search(r"名称：(.+)", message_content)
            description_match = re.search(r"描述：(.+)", message_content)
            file_size_match = re.search(r"📁 大小：(.+)", message_content)
            tags_match = re.search(r"🏷 标签：(.+)", message_content)
            link = self.extract_quark_link(message_content)

            name = name_match.group(1).strip() if name_match else ""
            description = description_match.group(1).strip() if description_match else ""
            file_size = file_size_match.group(1).strip() if file_size_match else ""
            tags = tags_match.group(1).strip() if tags_match else ""

            utc_timestamp = message.date
            local_timestamp = self.convert_to_local_time(utc_timestamp).strftime("%Y-%m-%d %H:%M:%S")

            return (name, description, link, file_size, tags, local_timestamp)
        except Exception as e:
            logger.error(f"解析消息时出错：{e}")
            return None

    async def insert_message(self, data, media_path):
        """将解析后的消息插入到数据库"""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                await db.execute('''
                    INSERT OR IGNORE INTO messages (name, description, link, file_size, tags, timestamp, image_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', data + (media_path,))
                await db.commit()
        except Exception as e:
            logger.error(f"插入数据时出错：{e}\n数据：{data}")

    async def query_messages(self, start_date: str, end_date: str) -> List[Tuple]:
        """按日期范围查询消息"""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                cursor = await db.execute('''
                    SELECT timestamp, name, description, link, file_size, tags, image_path
                    FROM messages
                    WHERE timestamp BETWEEN ? AND ?
                    ORDER BY timestamp DESC
                ''', (f"{start_date} 00:00:00", f"{end_date} 23:59:59"))
                rows = await cursor.fetchall()
                return rows
        except Exception as e:
            logger.error(f"查询消息时出错：{e}")
            return []

    async def handle_new_message(self, event, client, message_queue):
        """处理新收到的 Telegram 消息"""
        try:
            media_path = await self.save_media(client, event.message)
            parsed_message = self.parse_message(event.message)

            if not parsed_message:
                logger.warning("未能解析消息内容")
                return

            async with self.db_lock:
                await self.insert_message(parsed_message, media_path)

            message_queue.put({
                "text": parsed_message[1],
                "image_path": media_path,
                "timestamp": parsed_message[-1]
            })

            logger.info("新消息已插入并放入消息队列")

        except Exception as e:
            logger.error(f"处理消息时出错：{e}")

    async def listen_to_channel(self, client, default_channel, message_queue):
        """监听 Telegram 频道或群组消息"""
        @client.on(events.NewMessage(chats=default_channel))
        async def new_message_handler(event):
            logger.info("收到新消息")
            await self.handle_new_message(event, client, message_queue)

        try:
            logger.info(f"🔄 正在连接频道：{default_channel}")
            await client.run_until_disconnected()
        except Exception as e:
            logger.error(f"连接断开，错误：{e}")
        finally:
            await client.disconnect()
            logger.info("Telegram 客户端已断开连接")
