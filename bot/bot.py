"""QQ 群监测机器人 - NoneBot2 启动入口"""

import nonebot
from nonebot.adapters.onebot.v11 import Adapter as OneBotV11Adapter


def main():
    """初始化 NoneBot 并运行"""
    nonebot.init()

    # 注册 OneBot v11 适配器
    driver = nonebot.get_driver()
    driver.register_adapter(OneBotV11Adapter)

    # 加载插件
    nonebot.load_plugin("plugins.monitor")
    nonebot.load_plugin("plugins.admin")

    nonebot.run()


if __name__ == "__main__":
    main()
