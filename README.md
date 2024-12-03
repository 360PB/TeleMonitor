# TeleMonitor
Telegram频道 实时监听、查询和获取历史消息

暂时监听频道：https://t.me/NewQuark

群组: [@Quark_Share_Group](https://t.me/Quark_Share_Group)

##  功能

- [x] 实时监控@NewQuark新消息，获取后存入数据库
- [ ] 页面实时显示新消息
- [ ] 获取历史消息

### 获取Telegram api_id和**api_hash**

----

- 登录 Telegram 核心： [https://my.telegram.org](https://my.telegram.org/)

- 转到[“API开发工具”](https://my.telegram.org/apps)

- 获得基本地址以及用户授权所需的**api_id**和**api_hash**参数


## win整合包

- [ ] 待发布




## 安装

### 前提条件

- 需安装[Miniconda](https://repo.anaconda.com/miniconda/Miniconda3-latest-Windows-x86_64.exe)


### 步骤

1. **克隆仓库**

   ```
   git clone https://github.com/360PB/TeleMonitor.git
   cd TeleMonitor
   ```

2. **安装conda虚拟环境/依赖**

   ```
   conda_setup.bat
   ```

3. **运行应用程序**

   启动 Streamlit 应用程序：

   ```
   start.bat
   ```

   应用程序将在默认的网络浏览器中打开。

## 配置

**文件说明：**
```
.
├── config.py         # 加载配置文件
├── controller.py     # 核心逻辑控制模块
├── app.py           # Streamlit 页面展示模块
├── messages.db      # SQLite 数据库文件
├── media/           # 媒体文件目录
└── .env             # 配置文件
```
**手动创建.env**

```
# Telegram API 配置
TELEGRAM_API_ID="xxxxxxxx"
TELEGRAM_API_HASH="xxxxxxxxxxxxxxxxxxxxxxxxx"
DEFAULT_CHANNEL=@NewQuark

# 代理配置
PROXY_ENABLED=true
PROXY_TYPE=http
PROXY_ADDRESS=127.0.0.1
PROXY_PORT=7890
```



## 使用说明

参考页面



## 贡献

欢迎对本项目进行贡献！请通过提交问题或拉取请求与我们联系。

## 许可证

该项目基于 Apache 许可证开源。有关详细信息，请参阅 LICENSE 文件。
