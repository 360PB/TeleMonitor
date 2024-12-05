# controller.py
import asyncio
import os
import re
import logging
from typing import Optional, List, Tuple
from datetime import datetime, timezone
from queue import Queue

import aiosqlite
import socks
from telethon import TelegramClient, events
from telethon.errors import FloodWaitError
from telethon.tl.types import MessageMediaPhoto, Message

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
        self.db_lock = asyncio.Lock()
        self._client_lock = asyncio.Lock()
        self.stop_flag = False

    def set_stop_flag(self, value: bool):
        """设置停止标志"""
        self.stop_flag = value

    async def init_db(self, retry_count=3):
        """初始化数据库"""
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
                    return True
            except Exception as e:
                logger.warning(f"数据库初始化尝试 {attempt + 1} 失败: {e}")
                if attempt < retry_count - 1:
                    await asyncio.sleep(1)
                else:
                    logger.error("数据库初始化失败")
                    return False

    async def create_client(self):
        """创建并启动 Telegram 客户端"""
        async with self._client_lock:
            try:
                if self.client and self.client.is_connected():
                    return self.client

                proxy = None
                if self.config.PROXY_ENABLED:
                    proxy = (
                        socks.HTTP if self.config.PROXY_TYPE == "http" else socks.SOCKS5,
                        self.config.PROXY_ADDRESS,
                        self.config.PROXY_PORT
                    )

                self.client = TelegramClient(
                    self.config.SESSION_NAME,
                    self.config.TELEGRAM_API_ID,
                    self.config.TELEGRAM_API_HASH,
                    proxy=proxy,
                    loop=asyncio.get_event_loop()
                )

                if not self.client.is_connected():
                    await self.client.connect()

                if not await self.client.is_user_authorized():
                    await self.client.start()

                logger.info("Telegram 客户端启动成功")
                return self.client

            except Exception as e:
                logger.error(f"创建 Telegram 客户端失败: {e}")
                if self.client:
                    try:
                        await self.client.disconnect()
                    except:
                        pass
                self.client = None
                return None

    async def fetch_channel_history(self, channel_name=None, limit=100, offset_date=None):
        """获取频道历史消息并存储到数据库"""
        if not channel_name:
            channel_name = self.config.DEFAULT_CHANNEL

        try:
            if not self.client or not self.client.is_connected():
                self.client = await self.create_client()

            if not self.client:
                logger.error("无法创建 Telegram 客户端")
                return False, []

            # 获取频道实体
            entity = await self.client.get_entity(channel_name)
            messages_data = []

            async for message in self.client.iter_messages(
                entity,
                limit=limit,
                offset_date=offset_date,
                reverse=True  # 按时间正序获取
            ):
                if self.stop_flag:
                    logger.info("手动停止获取消息")
                    break

                try:
                    # 处理媒体文件
                    media_path = await self.save_media(message)
                    # 解析消息
                    parsed_message = self.parse_message(message)
                    
                    if parsed_message:
                        # 检查消息是否已存在
                        if not await self.is_message_stored(parsed_message[2]):
                            messages_data.append(parsed_message)
                            # 插入消息到数据库
                            async with self.db_lock:
                                await self.insert_message(parsed_message, media_path)

                except Exception as e:
                    logger.error(f"处理消息时出错: {e}")
                    continue

            logger.info(f"成功获取并存储了 {len(messages_data)} 条历史消息")
            return True, messages_data

        except Exception as e:
            logger.error(f"获取历史消息失败: {e}")
            return False, []

    async def is_message_stored(self, link: str) -> bool:
        """检查消息是否已存储"""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                cursor = await db.execute('SELECT 1 FROM messages WHERE link = ?', (link,))
                result = await cursor.fetchone()
                return result is not None
        except Exception as e:
            logger.error(f"检查消息存储状态时出错: {e}")
            return False

    async def save_media(self, message: Message) -> Optional[str]:
        """保存媒体文件"""
        try:
            if isinstance(message.media, MessageMediaPhoto):
                folder = "media"
                os.makedirs(folder, exist_ok=True)
                file_path = os.path.join(folder, f"{message.id}.jpg")
                
                if not os.path.exists(file_path):  # 避免重复下载
                    await self.client.download_media(message, file_path)
                return file_path
        except Exception as e:
            logger.error(f"保存媒体文件失败: {e}")
        return None

    def parse_message(self, message: Message) -> Optional[tuple]:
        """解析消息内容"""
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

            local_timestamp = self.convert_to_local_time(message.date).strftime("%Y-%m-%d %H:%M:%S")

            return (name, description, link, file_size, tags, local_timestamp)
        except Exception as e:
            logger.error(f"解析消息失败: {e}")
            return None

    @staticmethod
    def extract_quark_link(message_content: str) -> Optional[str]:
        """提取夸克网盘链接"""
        match = re.search(r'https://pan\.quark\.cn/s/[a-zA-Z0-9]+', message_content)
        return match.group(0) if match else None

    @staticmethod
    def convert_to_local_time(utc_datetime):
        """转换UTC时间为本地时间"""
        local_timezone = datetime.now(timezone.utc).astimezone().tzinfo
        return utc_datetime.astimezone(local_timezone)

    async def insert_message(self, data: tuple, media_path: Optional[str]):
        """插入消息到数据库"""
        try:
            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                await db.execute('''
                    INSERT OR IGNORE INTO messages 
                    (name, description, link, file_size, tags, timestamp, image_path)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', data + (media_path,))
                await db.commit()
        except Exception as e:
            logger.error(f"插入数据失败: {e}")

    async def query_messages(
        self,
        start_date: str,
        end_date: str,
        keyword: str = None,
        min_file_size: str = None,
        tags: str = None,
        sort_order: str = "时间降序"
    ) -> List[Tuple]:
        """查询消息"""
        try:
            query = '''
                SELECT timestamp, name, description, link, file_size, tags, image_path
                FROM messages
                WHERE timestamp BETWEEN ? AND ?
            '''
            params = [f"{start_date} 00:00:00", f"{end_date} 23:59:59"]

            # 添加过滤条件
            if keyword:
                query += " AND (description LIKE ? OR tags LIKE ?)"
                params.extend([f"%{keyword}%", f"%{keyword}%"])

            if tags:
                query += " AND tags LIKE ?"
                params.append(f"%{tags}%")

            # 添加排序
            if sort_order == "时间降序":
                query += " ORDER BY timestamp DESC"
            elif sort_order == "时间升序":
                query += " ORDER BY timestamp ASC"
            elif sort_order == "文件大小降序":
                query += " ORDER BY file_size DESC"
            elif sort_order == "文件大小升序":
                query += " ORDER BY file_size ASC"

            async with aiosqlite.connect(self.db_path, timeout=30.0) as db:
                cursor = await db.execute(query, params)
                results = await cursor.fetchall()

                # 处理文件大小过滤
                if min_file_size:
                    results = [r for r in results if self.compare_file_size(r[4], min_file_size)]

                return results

        except Exception as e:
            logger.error(f"查询消息失败: {e}")
            return []

    @staticmethod
    def compare_file_size(size1: str, size2: str) -> bool:
        """比较文件大小"""
        try:
            def convert_to_bytes(size_str: str) -> int:
                if not size_str:
                    return 0
                
                units = {'B': 1, 'KB': 1024, 'MB': 1024**2, 'GB': 1024**3, 'TB': 1024**4}
                number = float(re.findall(r'[\d.]+', size_str)[0])
                unit = re.findall(r'[A-Za-z]+', size_str)[0].upper()
                return int(number * units.get(unit, 1))

            return convert_to_bytes(size1) >= convert_to_bytes(size2)
        except Exception:
            return False

    async def listen_to_channel(
        self,
        channel_name: str,
        message_queue: Queue,
        use_proxy: bool = False,
        proxy_type: str = None,
        proxy_address: str = None,
        proxy_port: int = None
    ):
        """监听频道消息"""
        try:
            if not self.client or not self.client.is_connected():
                await self.create_client()

            @self.client.on(events.NewMessage(chats=channel_name))
            async def handler(event):
                if self.stop_flag:
                    return

                try:
                    media_path = await self.save_media(event.message)
                    parsed_message = self.parse_message(event.message)
                    
                    if parsed_message:
                        # 保存到数据库
                        async with self.db_lock:
                            await self.insert_message(parsed_message, media_path)
                        
                        # 发送到消息队列
                        message_queue.put({
                            "text": parsed_message[1],
                            "image_path": media_path,
                            "timestamp": parsed_message[5]
                        })
                        
                        logger.info(f"收到新消息: {parsed_message[1][:50]}...")
                        
                except Exception as e:
                    logger.error(f"处理新消息时出错: {e}")

            logger.info(f"开始监听频道: {channel_name}")
            await self.client.run_until_disconnected()
            
        except Exception as e:
            logger.error(f"监听频道时出错: {e}")
        finally:
            if self.client and not self.stop_flag:
                try:
                    await self.client.disconnect()
                except:
                    pass
