import asyncio
import logging
import os
import threading
from queue import Queue
from datetime import datetime, timedelta

import streamlit as st
import socks
from telethon import TelegramClient
from streamlit_autorefresh import st_autorefresh
import aiosqlite

from config import config
from controller import TelegramMessageController

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 设置页面配置
st.set_page_config(
    page_title="Telegram 消息管理系统",
    page_icon="📲",
    layout="wide",
    initial_sidebar_state="expanded",
)

class TelegramApp:
    def __init__(self):
        self.message_queue = Queue()
        self.messages = []
        self.controller = TelegramMessageController(config)
        self.listener_started = False
        self.client = None
        self.db_lock = asyncio.Lock()

    def run(self):
        st.title("📲 Telegram 消息管理系统")

        # 使用多栏布局
        col1, col2, col3 = st.columns(3)
        with col1:
            if st.button("实时监听", key="real_time_listener", help="实时监听 Telegram 频道或群组消息"):
                self.real_time_listener_page()
        with col2:
            if st.button("查询消息", key="query_messages", help="按日期范围查询消息历史"):
                self.query_messages_page()
        with col3:
            if st.button("获取历史消息", key="fetch_history", help="获取频道的历史消息"):
                self.fetch_history_page()

    def real_time_listener_page(self):
        with st.sidebar:
            st.subheader("监听设置")
            selected_channel = st.text_input(
                "频道或群组名称", 
                value=config.DEFAULT_CHANNEL, 
                placeholder="@example_channel",
                help="输入你想监听的 Telegram 频道或群组名称"
            )

            # 代理设置
            st.subheader("代理设置")
            use_proxy = st.checkbox(
                "启用代理", 
                value=config.PROXY_ENABLED,
                help="如果需要通过代理连接 Telegram，请勾选此选项"
            )
            proxy_type = st.selectbox(
                "代理类型", 
                ["http", "socks5"],
                index=0 if config.PROXY_TYPE == "http" else 1, 
                disabled=not use_proxy,
                help="选择代理类型"
            )
            proxy_address = st.text_input(
                "代理地址", 
                value=config.PROXY_ADDRESS, 
                disabled=not use_proxy,
                help="输入代理服务器的地址"
            )
            proxy_port = st.number_input(
                "代理端口", 
                value=config.PROXY_PORT, 
                step=1, 
                disabled=not use_proxy,
                help="输入代理服务器的端口"
            )

            if st.button("启动监听", key="start_listener"):
                if not self.listener_started:
                    st.sidebar.markdown("**监听已启动**")
                    threading.Thread(
                        target=self.async_start_listener, 
                        args=(selected_channel, use_proxy, proxy_type, proxy_address, proxy_port), 
                        daemon=True
                    ).start()
                    self.listener_started = True
                else:
                    st.sidebar.markdown("**监听已在运行**")

        # 使用容器来管理消息显示
        with st.container():
            st.subheader("实时消息")
            st.markdown("---")
            for msg in reversed(self.messages):  # 反转列表以显示最新消息在前面
                st.markdown(f"📅 **时间**：{msg['timestamp']}")
                st.markdown(msg["text"])
                if msg["image_path"] and os.path.exists(msg["image_path"]):
                    st.image(msg["image_path"], caption="收到的图片", width=400)
                st.markdown("---")

        st_autorefresh(interval=2000, key="datarefresh")

        while not self.message_queue.empty():
            msg = self.message_queue.get()
            self.messages.append(msg)
            if len(self.messages) > 50:
                self.messages.pop(0)

    def async_start_listener(self, selected_channel, use_proxy, proxy_type, proxy_address, proxy_port):
        # 使用 asyncio.run() 在新线程中运行异步事件循环
        asyncio.run(self.start_listener(selected_channel, use_proxy, proxy_type, proxy_address, proxy_port))

    async def start_listener(self, selected_channel, use_proxy, proxy_type, proxy_address, proxy_port):
        proxy = None
        if use_proxy:
            proxy = (
                socks.HTTP if proxy_type == "http" else socks.SOCKS5, 
                proxy_address, 
                int(proxy_port)
            )

        self.client = TelegramClient(
            config.SESSION_NAME,
            config.TELEGRAM_API_ID,
            config.TELEGRAM_API_HASH,
            proxy=proxy
        )

        try:
            # 初始化数据库
            async with self.db_lock:  # 使用锁
                async with aiosqlite.connect(self.controller.db_path, timeout=30.0) as db:
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
            
            # 启动客户端
            await self.client.start()
            
            # 监听频道
            await self.controller.listen_to_channel(self.client, selected_channel, self.message_queue)
        
        except Exception as e:
            logger.error(f"监听器运行出错：{e}")
        finally:
            # 安全断开连接
            if self.client:
                try:
                    await self.client.disconnect()
                except sqlite3.OperationalError:
                    logger.warning("数据库锁定，无法断开连接。")

    def query_messages_page(self):
        st.header("📚 消息查询")
        
        start_date = st.date_input(
            "开始日期", 
            value=datetime.now() - timedelta(days=7),
            help="选择查询的开始日期"
        )
        end_date = st.date_input(
            "结束日期", 
            value=datetime.now(),
            help="选择查询的结束日期"
        )

        if st.button("查询", key="query_button"):
            try:
                results = asyncio.run(
                    self.controller.query_messages(
                        start_date.strftime("%Y-%m-%d"), 
                        end_date.strftime("%Y-%m-%d")
                    )
                )

                if results:
                    st.subheader("查询结果")
                    for row in results:
                        st.markdown(f"📅 **时间**：{row[0]}")
                        st.markdown(f"**名称**：{row[1]}")
                        st.markdown(f"**描述**：{row[2]}")
                        st.markdown(f"**链接**：{row[3]}")
                        st.markdown(f"**文件大小**：{row[4]}")
                        st.markdown(f"**标签**：{row[5]}")
                        
                        if row[6] and os.path.exists(row[6]):
                            st.image(row[6], caption="图片", width=400)
                        
                        st.markdown("---")
                else:
                    st.warning("未找到符合条件的消息")
            except Exception as e:
                st.error(f"查询失败：{e}")

    def fetch_history_page(self):
        st.header("📜 获取历史消息")
        channel_name = st.text_input("输入频道名称", value=config.DEFAULT_CHANNEL, placeholder="@example_channel", help="输入你想获取历史消息的频道名称")
        limit = st.number_input("获取消息数量", min_value=1, max_value=1000, value=100, help="选择要获取的历史消息数量")
        offset_date = st.date_input("从指定日期开始获取", value=datetime.now() - timedelta(days=7), help="选择获取历史消息的起始日期")

        if st.button("获取历史消息", key="fetch_history_button"):
            try:
                # 确保 client 已经初始化
                if self.client is None:
                    st.error("客户端未初始化，请先启动监听。")
                    return

                if not self.client.is_connected():
                    st.info("客户端未连接，正在连接到 Telegram...")
                    asyncio.run(self.client.start())

                asyncio.run(self.controller.fetch_channel_history(
                    self.client, 
                    channel_name, 
                    limit=limit, 
                    offset_date=offset_date
                ))
                st.success("历史消息获取并处理成功！")
            except Exception as e:
                st.error(f"获取历史消息失败：{e}")

def main():
    # 在主线程中运行数据库初始化
    asyncio.run(TelegramMessageController(config).init_db())
    app = TelegramApp()
    app.run()

if __name__ == "__main__":
    main()
