"""独立包使用随包 CA 根证书；保留用户显式指定的企业证书配置。"""

import os
import certifi

os.environ.setdefault("SSL_CERT_FILE", certifi.where())
