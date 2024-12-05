# app.py
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

@st.cache_resource
def get_controller():
    """获取 TelegramMessageController 实例"""
    try:
        controller = TelegramMessageController(config)
        
        # 初始化数据库
        async def init():
            return await controller.init_db()
            
        # 使用 asyncio.run 运行初始化
        success = asyncio.run(init())
        if not success:
            st.error("数据库初始化失败")
            
        return controller
    except Exception as e:
        logger.error(f"控制器初始化失败: {e}")
        st.error(f"控制器初始化失败: {e}")
        return None

class TelegramApp:
    def __init__(self, controller: TelegramMessageController):
        self.message_queue = Queue()
        self.messages = []
        self.controller = controller
        self.listener_started = False
        self.listener_thread = None

    def run(self):
        """运行应用的主方法"""
        st.sidebar.title("🔧 功能菜单")
        
        page = st.sidebar.radio("选择功能", [
            "🌐 实时监听",
            "🔍 查询消息",
            "📜 获取历史消息"
        ])

        if page == "🌐 实时监听":
            self.real_time_listener_page()
        elif page == "🔍 查询消息":
            self.query_messages_page()
        else:
            self.fetch_history_page()

        st.sidebar.markdown("---")
        st.sidebar.info("Telegram 消息管理系统 v0.0.1")

    def start_listener_thread(self, channel_name, use_proxy, proxy_type, proxy_address, proxy_port):
        """在新线程中启动监听器"""
        def run_listener():
            try:
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)

                loop.run_until_complete(self._run_listener(
                    channel_name, use_proxy, proxy_type, proxy_address, proxy_port
                ))
            except Exception as e:
                logger.error(f"监听器运行出错: {e}")
            finally:
                loop.run_until_complete(loop.shutdown_asyncgens())
                loop.close()

        self.listener_thread = threading.Thread(target=run_listener, daemon=True)
        self.listener_thread.start()

    async def _run_listener(self, channel_name, use_proxy, proxy_type, proxy_address, proxy_port):
        """运行监听器的异步方法"""
        try:
            if not self.controller.client or not self.controller.client.is_connected():
                self.controller.client = await self.controller.create_client()

            if self.controller.client and self.controller.client.is_connected():
                await self.controller.listen_to_channel(
                    channel_name,
                    self.message_queue,
                    use_proxy=use_proxy,
                    proxy_type=proxy_type,
                    proxy_address=proxy_address,
                    proxy_port=proxy_port
                )
        except Exception as e:
            logger.error(f"运行监听器时出错: {e}")

    def stop_listener_thread(self):
        """停止监听线程"""
        try:
            self.controller.set_stop_flag(True)
            if self.listener_thread and self.listener_thread.is_alive():
                self.listener_thread.join(timeout=5)
        except Exception as e:
            logger.error(f"停止监听线程时出错: {e}")

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

        # 添加状态管理
        if 'fetching' not in st.session_state:
            st.session_state.fetching = False
        if 'messages' not in st.session_state:
            st.session_state.messages = []

        # 创建两列用于放置按钮
        col1, col2 = st.columns(2)
        
        with col1:
            fetch_btn = st.button(
                "获取历史消息",
                disabled=st.session_state.fetching,
                key="fetch_history"
            )

        with col2:
            stop_btn = st.button(
                "停止获取",
                disabled=not st.session_state.fetching,
                key="stop_fetch"
            )

        # 获取历史消息
        if fetch_btn and not st.session_state.fetching:
            try:
                st.session_state.fetching = True
                st.session_state.messages = []
                self.controller.set_stop_flag(False)  # 重置停止标志
                
                def run_fetch():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        return loop.run_until_complete(
                            self.controller.fetch_channel_history(
                                channel_name, limit, offset_date
                            )
                        )
                    finally:
                        loop.close()

                success, messages = run_fetch()
                
                if success and messages:
                    st.session_state.messages = messages
                    st.success(f"成功获取 {len(messages)} 条历史消息！")
                else:
                    st.error("获取历史消息失败或没有找到消息")
                    
            except Exception as e:
                st.error(f"获取历史消息时出错：{str(e)}")
            finally:
                st.session_state.fetching = False

        # 处理停止按钮
        if stop_btn and st.session_state.fetching:
            self.controller.set_stop_flag(True)
            st.warning("正在停止获取消息...")

        # 显示已获取的消息
        if st.session_state.messages:
            st.subheader(f"已获取 {len(st.session_state.messages)} 条消息")
            for idx, msg in enumerate(st.session_state.messages):
                with st.expander(
                    f"📅 {msg['timestamp']} - {msg['name']}",
                    expanded=False
                ):
                    col1, col2 = st.columns([2, 1])
                    with col1:
                        st.markdown(f"**描述**: {msg['description']}")
                        if msg['link']:
                            st.markdown(f"**链接**: {msg['link']}")
                        if msg['file_size']:
                            st.markdown(f"**文件大小**: {msg['file_size']}")
                        if msg['tags']:
                            st.markdown(f"**标签**: {msg['tags']}")
                    with col2:
                        if msg['image_path'] and os.path.exists(msg['image_path']):
                            st.image(msg['image_path'], width=200)

    def query_messages_page(self):
        st.header("🔍 消息查询")

        # 日期选择
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
        with st.expander("高级过滤选项"):
            filter_col1, filter_col2 = st.columns(2)
            with filter_col1:
                keyword = st.text_input(
                    "关键词搜索",
                    placeholder="输入搜索关键词",
                    help="在描述和标签中搜索"
                )
                min_file_size = st.text_input(
                    "最小文件大小",
                    placeholder="例如: 100MB",
                    help="筛选大于指定大小的文件"
                )
            with filter_col2:
                tags = st.text_input(
                    "标签筛选",
                    placeholder="输入标签关键词",
                    help="按标签筛选消息"
                )
                sort_order = st.selectbox(
                    "排序方式",
                    ["时间降序", "时间升序", "文件大小降序", "文件大小升序"],
                    help="选择结果的排序方式"
                )

        # 查询按钮
        if st.button("查询消息", key="query_btn"):
            try:
                with st.spinner('正在查询消息...'):
                    def run_query():
                        loop = asyncio.new_event_loop()
                        asyncio.set_event_loop(loop)
                        try:
                            return loop.run_until_complete(
                                self.controller.query_messages(
                                    start_date.strftime("%Y-%m-%d"),
                                    end_date.strftime("%Y-%m-%d"),
                                    keyword=keyword,
                                    min_file_size=min_file_size,
                                    tags=tags,
                                    sort_order=sort_order
                                )
                            )
                        finally:
                            loop.close()

                    results = run_query()

                    if results:
                        st.success(f"找到 {len(results)} 条消息")
                        
                        # 创建消息显示容器
                        messages_container = st.container()
                        with messages_container:
                            for msg in results:
                                with st.expander(f"{msg[0]} - {msg[1]}", expanded=False):
                                    cols = st.columns([2, 1])
                                    with cols[0]:
                                        st.markdown(f"**描述**: {msg[2]}")
                                        st.markdown(f"**链接**: {msg[3]}")
                                        st.markdown(f"**大小**: {msg[4]}")
                                        st.markdown(f"**标签**: {msg[5]}")
                                    with cols[1]:
                                        if msg[6] and os.path.exists(msg[6]):
                                            st.image(msg[6], width=200)
                    else:
                        st.warning("未找到符合条件的消息")

            except Exception as e:
                st.error(f"查询失败: {e}")
                logger.error(f"查询消息时出错: {e}")

    def real_time_listener_page(self):
        st.header("🌐 实时监听")

        # 添加会话状态管理
        if 'listener_started' not in st.session_state:
            st.session_state.listener_started = False
        if 'listener_messages' not in st.session_state:
            st.session_state.listener_messages = []

        # 监听设置
        channel_col1, channel_col2 = st.columns(2)
        with channel_col1:
            channel_name = st.text_input(
                "频道名称",
                value=config.DEFAULT_CHANNEL,
                placeholder="@example_channel",
                help="输入要监听的频道名称"
            )

        with channel_col2:
            refresh_interval = st.number_input(
                "刷新间隔(秒)",
                min_value=1,
                max_value=60,
                value=2,
                help="设置页面刷新间隔"
            )

        # 代理设置
        with st.expander("代理设置"):
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
                        index=0 if config.PROXY_TYPE == "http" else 1
                    )
                with proxy_col2:
                    proxy_address = st.text_input(
                        "代理地址",
                        value=config.PROXY_ADDRESS
                    )
                with proxy_col3:
                    proxy_port = st.number_input(
                        "代理端口",
                        value=config.PROXY_PORT,
                        step=1
                    )

        # 监听控制
        col1, col2 = st.columns(2)
        with col1:
            if st.button(
                "开始监听" if not st.session_state.listener_started else "停止监听",
                key="toggle_listener"
            ):
                if not st.session_state.listener_started:
                    try:
                        self.controller.set_stop_flag(False)
                        self.start_listener_thread(
                            channel_name,
                            use_proxy,
                            proxy_type,
                            proxy_address,
                            proxy_port
                        )
                        st.session_state.listener_started = True
                        st.success("开始监听消息")
                    except Exception as e:
                        st.error(f"启动监听失败: {e}")
                        st.session_state.listener_started = False
                else:
                    try:
                        self.stop_listener_thread()
                        st.session_state.listener_started = False
                        st.warning("已停止监听")
                    except Exception as e:
                        st.error(f"停止监听失败: {e}")

        with col2:
            if st.button("清空消息", key="clear_messages"):
                self.messages.clear()
                st.session_state.listener_messages = []
                st.success("已清空消息列表")

        # 显示消息
        st.subheader("实时消息")
        message_container = st.container()
        
        # 处理消息队列
        try:
            while not self.message_queue.empty():
                try:
                    msg = self.message_queue.get_nowait()
                    if msg not in st.session_state.listener_messages:
                        st.session_state.listener_messages.append(msg)
                        if len(st.session_state.listener_messages) > 50:
                            st.session_state.listener_messages.pop(0)
                except Exception as e:
                    logger.error(f"处理单条消息时出错: {e}")
                    continue
        except Exception as e:
            logger.error(f"处理消息队列时出错: {e}")

        # 显示消息
        with message_container:
            if st.session_state.listener_messages:
                for msg in reversed(st.session_state.listener_messages[-10:]):
                    try:
                        with st.expander(f"📅 {msg['timestamp']}", expanded=False):
                            st.markdown(msg["text"])
                            if msg.get("image_path") and os.path.exists(msg["image_path"]):
                                st.image(msg["image_path"], width=200)
                    except Exception as e:
                        logger.error(f"显示消息时出错: {e}")
                        continue
            else:
                st.info("暂无消息")

        # 仅在监听活动时自动刷新
        if st.session_state.listener_started:
            st_autorefresh(interval=refresh_interval * 1000, key="realtime_refresh")

        # 显示监听状态
        status_container = st.empty()
        if st.session_state.listener_started:
            status_container.success("✅ 正在监听消息...")
        else:
            status_container.info("⏸️ 监听已停止")

    def cleanup(self):
        """清理资源"""
        try:
            # 停止监听
            if st.session_state.get('listener_started', False):
                self.stop_listener_thread()
                st.session_state.listener_started = False

            # 清理消息队列
            while not self.message_queue.empty():
                self.message_queue.get()
            
            # 断开客户端连接
            if self.controller.client:
                def run_disconnect():
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    try:
                        loop.run_until_complete(self.controller.client.disconnect())
                    finally:
                        loop.close()
                
                run_disconnect()
            
            logger.info("资源清理完成")
        except Exception as e:
            logger.error(f"清理资源时出错: {e}")

def main():
    try:
        # 获取控制器实例
        controller = get_controller()
        if controller is None:
            st.error("无法初始化应用，请检查配置和日志")
            return

        # 创建应用实例
        app = TelegramApp(controller)
        
        # 运行应用
        app.run()
        
    except Exception as e:
        st.error(f"应用运行出错: {e}")
        logger.error(f"应用运行出错: {e}")
    finally:
        # 确保在应用关闭时清理资源
        if 'app' in locals():
            app.cleanup()

if __name__ == "__main__":
    main()
