import asyncio
import logging
import os
import threading
import sys
from queue import Queue
from datetime import datetime, timedelta

import streamlit as st
import socks
from telethon import TelegramClient
from streamlit_autorefresh import st_autorefresh
import aiosqlite

from config import config
from controller import TelegramMessageController

# 设置适用于 Windows 的事件循环策略
if sys.platform.startswith('win'):
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

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

# 使用 Streamlit 的缓存机制确保 TelegramMessageController 实例唯一
@st.cache_resource
def get_controller():
    controller = TelegramMessageController(config)
    # 在后台线程中初始化数据库
    init_thread = threading.Thread(target=asyncio.run, args=(controller.init_db(),), daemon=True)
    init_thread.start()
    return controller

# 管理 asyncio 事件循环的后台线程
class AsyncioEventLoopThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.loop = asyncio.new_event_loop()

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

# 启动事件循环线程
loop_thread = AsyncioEventLoopThread()
loop_thread.start()

# 提供提交协程到事件循环的方法
def submit_coroutine(coroutine):
    """将协程提交到后台事件循环并等待结果"""
    future = asyncio.run_coroutine_threadsafe(coroutine, loop_thread.loop)
    try:
        return future.result(timeout=60)  # 设置超时为60秒
    except Exception as e:
        logger.error(f"提交协程时出错：{e}")
        return None

# 定义 TelegramApp 类
class TelegramApp:
    def __init__(self, controller: TelegramMessageController):
        self.message_queue = Queue()
        self.messages = []
        self.controller = controller
        self.listener_started = False

    def run(self):
        # 使用侧边栏作为主导航
        st.sidebar.title("🔧 功能菜单")

        # 使用单选按钮进行页面切换
        page = st.sidebar.radio("选择功能", [
            "🌐 实时监听",
            "🔍 查询消息",
            "📜 获取历史消息"
        ])

        # 显示选定的页面
        if page == "🌐 实时监听":
            self.real_time_listener_page()
        elif page == "🔍 查询消息":
            self.query_messages_page()
        else:
            self.fetch_history_page()

        # 侧边栏底部添加系统状态和信息
        st.sidebar.markdown("---")
        st.sidebar.info("Telegram 消息管理系统 v0.0.1")

    def fetch_history_page(self):
        st.header("📜 获取历史消息")

        # 创建两列来布局输入参数
        channel_col1, channel_col2 = st.columns(2)

        with channel_col1:
            channel_name = st.text_input(
                "频道名称",
                value=config.DEFAULT_CHANNEL,
                placeholder="@example_channel",
                help="输入你想获取历史消息的频道名称"
            )

        with channel_col2:
            limit = st.number_input(
                "获取消息数量",
                min_value=1,
                max_value=1000,
                value=100,
                help="选择要获取的历史消息数量"
            )

        offset_date = st.date_input(
            "从指定日期开始获取",
            value=datetime.now() - timedelta(days=7),
            help="选择获取历史消息的起始日期"
        )

        # 初始化客户端按钮
        if 'init_client_done' not in st.session_state:
            st.session_state.init_client_done = False

        init_client_btn = st.button("初始化 Telegram 客户端", key="init_client_fetch")

        if init_client_btn and not st.session_state.init_client_done:
            try:
                # 提交创建客户端的协程
                client = submit_coroutine(self.controller.create_client())
                if client:
                    st.success("Telegram 客户端初始化成功!")
                    st.session_state.init_client_done = True
                else:
                    st.error("客户端初始化失败")
            except Exception as e:
                st.error(f"初始化出错: {e}")

        # 获取历史消息按钮
        fetch_btn = st.button("获取历史消息", key="fetch_history_fetch")

        if fetch_btn:
            try:
                # 提交获取历史消息的协程
                result = submit_coroutine(
                    self.controller.fetch_channel_history(
                        channel_name,
                        limit=limit,
                        offset_date=offset_date
                    )
                )
                if result:
                    st.success("历史消息获取并处理成功！🎉")
                else:
                    st.error("获取历史消息失败")
            except Exception as e:
                st.error(f"获取历史消息出错：{e}")

        # 代理设置开关
        use_proxy = st.checkbox(
            "启用代理",
            value=config.PROXY_ENABLED,
            help="如果需要通过代理连接 Telegram，请勾选此选项"
        )

        if use_proxy:
            proxy_col1, proxy_col2, proxy_col3 = st.columns(3)

            with proxy_col1:
                proxy_type = st.selectbox(
                    "代理类型",
                    ["http", "socks5"],
                    index=0 if config.PROXY_TYPE == "http" else 1,
                    help="选择代理类型"
                )

            with proxy_col2:
                proxy_address = st.text_input(
                    "代理地址",
                    value=config.PROXY_ADDRESS,
                    help="输入代理服务器的地址"
                )

            with proxy_col3:
                proxy_port = st.number_input(
                    "代理端口",
                    value=config.PROXY_PORT,
                    step=1,
                    help="输入代理服务器的端口"
                )
        else:
            proxy_type, proxy_address, proxy_port = None, None, None

    def query_messages_page(self):
        st.header("🔍 消息查询")

        # 使用列来布置日期选择器
        date_col1, date_col2 = st.columns(2)

        with date_col1:
            start_date = st.date_input(
                "开始日期",
                value=datetime.now() - timedelta(days=7),
                help="选择查询的开始日期"
            )

        with date_col2:
            end_date = st.date_input(
                "结束日期",
                value=datetime.now(),
                help="选择查询的结束日期"
            )

        # 高级过滤选项
        with st.expander("高级过滤"):
            filter_col1, filter_col2 = st.columns(2)

            with filter_col1:
                min_file_size = st.text_input("最小文件大小", placeholder="例如: 100MB")

            with filter_col2:
                tags_filter = st.text_input("标签筛选", placeholder="输入标签关键词")

        # 查询按钮
        query_btn = st.button("查询", key="query_button")

        if query_btn:
            try:
                # 提交查询消息的协程
                results = submit_coroutine(
                    self.controller.query_messages(
                        start_date.strftime("%Y-%m-%d"),
                        end_date.strftime("%Y-%m-%d")
                    )
                )

                if results:
                    st.subheader(f"🔎 查询到 {len(results)} 条消息")
                    for row in results:
                        with st.expander(f"📅 {row[0]}"):
                            st.markdown(f"**名称**：{row[1]}")
                            st.markdown(f"**描述**：{row[2]}")
                            st.markdown(f"**链接**：{row[3]}")
                            st.markdown(f"**文件大小**：{row[4]}")
                            st.markdown(f"**标签**：{row[5]}")

                            if row[6] and os.path.exists(row[6]):
                                st.image(row[6], caption="图片", width=300)
                else:
                    st.warning("未找到符合条件的消息")
            except Exception as e:
                st.error(f"查询失败：{e}")

    def real_time_listener_page(self):
        st.header("🌐 实时监听")

        # 创建两列来布置输入参数
        listener_col1, listener_col2 = st.columns(2)

        with listener_col1:
            channel_name = st.text_input(
                "频道名称",
                value=config.DEFAULT_CHANNEL,
                placeholder="@example_channel",
                help="输入你想监听的频道名称"
            )

        with listener_col2:
            limit = st.number_input(
                "获取消息数量",
                min_value=1,
                max_value=1000,
                value=100,
                help="选择要获取的消息数量"
            )

        # 代理设置开关
        use_proxy = st.checkbox(
            "启用代理",
            value=config.PROXY_ENABLED,
            help="如果需要通过代理连接 Telegram，请勾选此选项"
        )

        if use_proxy:
            proxy_col1, proxy_col2, proxy_col3 = st.columns(3)

            with proxy_col1:
                proxy_type = st.selectbox(
                    "代理类型",
                    ["http", "socks5"],
                    index=0 if config.PROXY_TYPE == "http" else 1,
                    help="选择代理类型"
                )

            with proxy_col2:
                proxy_address = st.text_input(
                    "代理地址",
                    value=config.PROXY_ADDRESS,
                    help="输入代理服务器的地址"
                )

            with proxy_col3:
                proxy_port = st.number_input(
                    "代理端口",
                    value=config.PROXY_PORT,
                    step=1,
                    help="输入代理服务器的端口"
                )
        else:
            proxy_type, proxy_address, proxy_port = None, None, None

        # 启动监听按钮
        if 'listener_started_rt' not in st.session_state:
            st.session_state.listener_started_rt = False

        start_listener_btn = st.button("启动监听", key="start_listener_rt")

        if start_listener_btn and not st.session_state.listener_started_rt:
            st.session_state.listener_started_rt = True
            st.success("监听已启动 🚀")
            # 启动监听器线程
            threading.Thread(
                target=self.async_start_listener,
                args=(channel_name, use_proxy, proxy_type, proxy_address, proxy_port),
                daemon=True
            ).start()

        # 消息显示区域
        st.subheader("🔊 实时消息")
        st.markdown("---")

        # 使用 expander 来控制消息显示
        with st.expander("查看最近消息", expanded=True):
            for msg in reversed(self.messages[-10:]):  # 只显示最近10条消息
                st.markdown(f"📅 **时间**：{msg['timestamp']}")
                st.markdown(msg["text"])
                if msg["image_path"] and os.path.exists(msg["image_path"]):
                    st.image(msg["image_path"], caption="收到的图片", width=300)
                st.markdown("---")

        # 自动刷新
        st_autorefresh(interval=2000, key="datarefresh_rt")

        # 处理消息队列
        while not self.message_queue.empty():
            msg = self.message_queue.get()
            self.messages.append(msg)
            if len(self.messages) > 50:
                self.messages.pop(0)
            logger.info("实时消息已更新")

    def async_start_listener(self, selected_channel, use_proxy, proxy_type, proxy_address, proxy_port):
        """在后台提交启动监听器的协程"""
        submit_coroutine(
            self.start_listener(selected_channel, use_proxy, proxy_type, proxy_address, proxy_port)
        )

    async def start_listener(self, selected_channel, use_proxy, proxy_type, proxy_address, proxy_port):
        proxy = None
        if use_proxy:
            proxy = (
                socks.HTTP if proxy_type == "http" else socks.SOCKS5,
                proxy_address,
                int(proxy_port)
            )

        try:
            # 启动 Telegram 客户端
            client = await self.controller.create_client()

            if not client:
                st.error("Telegram 客户端启动失败。请检查日志以获取更多信息。")
                return

            # 监听频道
            await self.controller.listen_to_channel(client, selected_channel, self.message_queue)

        except Exception as e:
            logger.error(f"监听器运行出错：{e}")
        finally:
            # 安全断开连接
            if client:
                try:
                    await client.disconnect()
                except Exception as disconnect_error:
                    logger.warning(f"断开连接时出错：{disconnect_error}")

def main():
    # 获取 TelegramMessageController 实例
    controller = get_controller()

    app = TelegramApp(controller)
    app.run()

if __name__ == "__main__":
    main()
