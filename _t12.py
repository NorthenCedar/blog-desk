# -*- coding: utf-8 -*-
"""发布历史兼容入口：普通 Git 取代旧 API 对齐，不再要求 soft reset。

全部夹具为临时目录中的真实 Git 仓库与本地 bare 远端，不访问网络或凭据。
覆盖本地多笔提交、远端并发前进、源码隔离和校验快照。
"""
import unittest
from _test_publish_safety import PublishSafetyTests

if __name__ == '__main__':
    unittest.main(verbosity=2)
