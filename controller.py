import asyncio
import os
import re
import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone

import aiosqlite  # 确保导入 aiosqlite
import socks
from telethon import TelegramClient, events
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

    async def init_db(self):
        """初始化数据库连接和表结构"""
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            async with db.cursor() as cursor:
                await cursor.execute('''
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

    def parse_message(self, event):
        """解析 Telegram 消息内容"""
        try:
            message_content = event.message.message or ""
            name_match = re.search(r"名称：(.+)", message_content)
            description_match = re.search(r"描述：(.+)", message_content)
            file_size_match = re.search(r"📁 大小：(.+)", message_content)
            tags_match = re.search(r"🏷 标签：(.+)", message_content)
            link = self.extract_quark_link(message_content)

            name = name_match.group(1).strip() if name_match else ""
            description = description_match.group(1).strip() if description_match else ""
            file_size = file_size_match.group(1).strip() if file_size_match else ""
            tags = tags_match.group(1).strip() if tags_match else ""

            utc_timestamp = event.message.date
            local_timestamp = self.convert_to_local_time(utc_timestamp).strftime("%Y-%m-%d %H:%M:%S")

            return (name, description, link, file_size, tags, local_timestamp)
        except Exception as e:
            logger.error(f"解析消息时出错：{e}")
            return None

    async def insert_message(self, data, media_path):
        """将解析后的消息插入到数据库"""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                async with db.cursor() as cursor:
                    await cursor.execute('''
                        INSERT OR IGNORE INTO messages (name, description, link, file_size, tags, timestamp, image_path)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', data + (media_path,))
                    await db.commit()
        except Exception as e:
            logger.error(f"插入数据时出错：{e}\n数据：{data}")

    async def query_messages(self, start_date: str, end_date: str) -> List[Tuple]:
        """按日期范围查询消息"""
        async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
            cursor = await db.execute('''
                SELECT timestamp, name, description, link, file_size, tags, image_path
                FROM messages
                WHERE timestamp BETWEEN ? AND ?
                ORDER BY timestamp DESC
            ''', (start_date, end_date))
            rows = await cursor.fetchall()
            return rows

    async def handle_new_message(self, event, client, message_queue):
        """处理新收到的 Telegram 消息"""
        try:
            media_path = await self.save_media(client, event.message)
            parsed_message = self.parse_message(event)
            
            if not parsed_message:
                return

            await self.insert_message(parsed_message, media_path)

            message_queue.put({
                "text": parsed_message[1],
                "image_path": media_path,
                "timestamp": parsed_message[-1]
            })
        except Exception as e:
            logger.error(f"处理消息时出错：{e}")

    async def listen_to_channel(self, client, default_channel, message_queue):
        """监听 Telegram 频道或群组消息"""
        @client.on(events.NewMessage(chats=default_channel))
        async def new_message_handler(event):
            await self.handle_new_message(event, client, message_queue)

        try:
            logger.info(f"🔄 正在连接频道：{default_channel}")
            await client.run_until_disconnected()
        except Exception as e:
            logger.error(f"连接断开，错误：{e}")
        finally:
            await client.disconnect()

    async def fetch_channel_history(self, client, channel_name, limit=100, offset_date=None):
        """获取频道的历史消息"""
        try:
            if client is None or not client.is_connected():
                logger.error("客户端未连接，无法获取历史消息。")
                return

            entity = await client.get_entity(channel_name)
            messages = await client.get_messages(entity, limit=limit, offset_date=offset_date)
            for message in messages:
                await self.handle_new_message(events.NewMessage.Event(message), client, self.message_queue)
            logger.info(f"已获取并处理了 {len(messages)} 条历史消息")
        except Exception as e:
            logger.error(f"获取历史消息时出错：{e}")
